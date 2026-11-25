"""Experimental Zwift sign-in and device authorization.

Protocol reference: https://github.com/thomaschampagne/zwiftboard
See THIRD_PARTY_ZWIFTBOARD.txt for the upstream MIT notice.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

TOKEN_URL = "https://secure.zwift.com/auth/realms/zwift/protocol/openid-connect/token"
DEVICE_URL = "https://us-or-rly101.zwift.com/api/d-lock-service/device/authenticate"
CLIENT_ID = "Zwift Game Client"
AUTH_ERRORS = {"invalid_grant", "invalid_client", "unauthorized_client"}

class AuthError(RuntimeError):
    pass

class Rejected(AuthError):
    pass

class Unauthorized(AuthError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def exchange(url, data, headers, limit):
    if url not in (TOKEN_URL, DEVICE_URL):
        raise AuthError("Adresse Zwift inattendue")
    try:
        with build_opener(NoRedirect()).open(Request(url, data=data, headers=headers, method="POST"), timeout=10) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise AuthError("Reponse Zwift trop volumineuse")
            return body
    except HTTPError as exc:
        if url == TOKEN_URL:
            try:
                code = json.loads(exc.read(8192)).get("error")
            except (ValueError, AttributeError):
                code = None
            if code in AUTH_ERRORS:
                raise Rejected("Connexion Zwift refusee. Verifier le compte ou renouveler la session.") from None
        if url == DEVICE_URL and exc.code == 401:
            raise Unauthorized("Session Zwift expiree") from None
        raise AuthError(f"Service Zwift : HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise AuthError("Service Zwift inaccessible") from None

class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

def dpapi(data, protect):
    if os.name != "nt":
        raise AuthError("Conservation chiffree disponible uniquement sous Windows")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming = Blob(len(data), source)
    outgoing = Blob()
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    # User-bound protection; never use CRYPTPROTECT_LOCAL_MACHINE.
    try:
        if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise AuthError("Protection Windows de la session indisponible")
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        if outgoing.pbData:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel.LocalFree(outgoing.pbData)
        ctypes.memset(source, 0, len(data))

def session_path():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ZwiftController" / "zwift-session.dat"

def challenge_payload(frame):
    return bytes(frame[3:]) if len(frame) >= 4 and bytes(frame[:3]) == b"\xff\x03\x00" else None

def signed_frame(reply):
    if not reply:
        raise AuthError("Reponse Zwift vide")
    return b"\xff\x04\x00" + reply

class Session:
    def __init__(self, path=None, transport=exchange, protection=dpapi, clock=time.time):
        self.path = Path(path) if path is not None else session_path()
        self.transport = transport
        self.protection = protection
        self.clock = clock
        self.lock = threading.RLock()
        self.tokens = None
        self.persist = False
        self.storage_warning = False
        try:
            raw = self.protection(self.path.read_bytes(), False)
            tokens = json.loads(raw)
            if (isinstance(tokens, dict) and isinstance(tokens.get("refresh_token"), str)
                    and tokens["refresh_token"] and isinstance(tokens.get("access_token"), str)
                    and isinstance(tokens.get("expires_at"), (int, float))):
                self.tokens = tokens
                self.persist = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AuthError, TypeError):
            self.storage_warning = True

    @property
    def ready(self):
        tokens = self.tokens
        return tokens is not None and bool(tokens.get("refresh_token"))

    def _save(self):
        if not self.persist:
            return
        temp = None
        try:
            data = self.protection(json.dumps(self.tokens).encode("utf-8"), True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".zwift-", delete=False) as f:
                temp = Path(f.name)
                f.write(data)
            os.replace(temp, self.path)
            self.storage_warning = False
        except (OSError, AuthError, ValueError):
            self.storage_warning = True
        finally:
            if temp:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def _token(self, form, old_refresh=""):
        form = {**form, "client_id": CLIENT_ID}
        raw = self.transport(TOKEN_URL, urlencode(form).encode("ascii"),
                             {"Content-Type": "application/x-www-form-urlencoded"}, 1024*1024)
        try:
            data = json.loads(raw)
            access = data["access_token"]
            refresh = data.get("refresh_token") or old_refresh
            expires = self.clock() + max(0, int(data.get("expires_in", 0)))
            if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise AuthError("Reponse de connexion Zwift invalide") from None
        return {"access_token": access, "refresh_token": refresh, "expires_at": expires}

    def login(self, email, password, remember=True):
        with self.lock:
            new = self._token({"grant_type":"password", "username":email, "password":password})
            if not remember:
                # Do not leave an older remembered account behind.
                self.path.unlink(missing_ok=True)
            self.tokens = new
            self.persist = remember
            self.storage_warning = False
            self._save()

    def logout(self):
        with self.lock:
            self.tokens = None
            self.persist = False
            self.path.unlink(missing_ok=True)
            self.storage_warning = False

    def _refresh(self):
        refresh = self.tokens["refresh_token"]
        try:
            self.tokens = self._token({"grant_type":"refresh_token", "refresh_token":refresh}, refresh)
        except Rejected:
            self.tokens = None
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                self.storage_warning = True
            raise
        self._save()

    def _solve(self, challenge):
        return signed_frame(self.transport(DEVICE_URL, challenge,
            {"Authorization":"Bearer " + self.tokens["access_token"],
             "Content-Type":"application/x-protobuf-lite", "Accept":"application/x-protobuf-lite"}, 65536))

    def solve(self, challenge):
        with self.lock:
            if not self.ready:
                raise Rejected("Connexion au compte Zwift requise")
            if self.clock() + 60 >= self.tokens.get("expires_at", 0):
                self._refresh()
            try:
                return self._solve(challenge)
            except Unauthorized:
                self._refresh()
                return self._solve(challenge)

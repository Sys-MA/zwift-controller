import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs

from zwift_auth import Session, TOKEN_URL, DEVICE_URL, AuthError, Rejected, Unauthorized, challenge_payload, signed_frame, dpapi
import os

class FakeService:
    def __init__(self):
        self.calls = []
        self.unauthorized = False
        self.reject_refresh = False
        self.offline = False
    def __call__(self, url, data, headers, limit):
        self.calls.append(url)
        if self.offline:
            raise AuthError("offline")
        if url == TOKEN_URL:
            form = parse_qs(data.decode())
            if form["grant_type"] == ["refresh_token"] and self.reject_refresh:
                raise Rejected("expired")
            return json.dumps({"access_token":"access", "refresh_token":"refresh", "expires_in":300}).encode()
        if self.unauthorized:
            self.unauthorized = False
            raise Unauthorized()
        assert headers["Authorization"] == "Bearer access"
        return b"server-signature"

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.dat"
        self.service = FakeService()
        self.now = 1000
        self.protect = lambda data, encrypt: bytes(x ^ 85 for x in data)
        self.session = Session(self.path, self.service, self.protect, lambda:self.now)

    def test_frames(self):
        self.assertEqual(challenge_payload(b"\xff\x03\x00abc"), b"abc")
        self.assertIsNone(challenge_payload(b"\xff\x03\x00"))
        self.assertIsNone(challenge_payload(b"\xff\x05\x00abc"))
        self.assertEqual(signed_frame(b"abc"), b"\xff\x04\x00abc")
        with self.assertRaises(AuthError):
            signed_frame(b"")

    def test_login_and_persistence(self):
        self.session.login("fake@example.invalid", "fake-password")
        self.assertTrue(self.session.ready)
        self.assertNotIn(b"fake-password", self.path.read_bytes())
        self.assertNotIn(b"refresh", self.path.read_bytes())
        restored = Session(self.path, self.service, self.protect, lambda:self.now)
        self.assertTrue(restored.ready)
        self.assertEqual(restored.solve(b"challenge"), b"\xff\x04\x00server-signature")
        restored.logout()
        self.assertFalse(restored.ready)
        self.assertFalse(self.path.exists())

    def test_refresh_and_retry(self):
        self.session.login("fake", "fake", remember=False)
        self.assertFalse(self.path.exists())
        self.now += 280
        self.service.unauthorized = True
        self.session.solve(b"challenge")
        self.assertEqual(self.service.calls, [TOKEN_URL,TOKEN_URL,DEVICE_URL,TOKEN_URL,DEVICE_URL])

    def test_offline_preserves_session(self):
        self.session.login("fake", "fake")
        self.service.offline = True
        with self.assertRaises(AuthError):
            self.session.solve(b"challenge")
        self.assertTrue(self.session.ready)
        self.assertTrue(self.path.exists())

    def test_rejected_refresh_clears_session(self):
        self.session.login("fake", "fake")
        self.now += 280
        self.service.reject_refresh = True
        with self.assertRaises(Rejected):
            self.session.solve(b"challenge")
        self.assertFalse(self.session.ready)
        self.assertFalse(self.path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_real_dpapi_roundtrip(self):
        raw = b"unit test dummy token"
        encrypted = dpapi(raw, True)
        self.assertNotEqual(raw, encrypted)
        self.assertEqual(dpapi(encrypted, False), raw)

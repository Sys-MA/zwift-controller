"""Opt-in installation of stable releases from the project's GitHub repository."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from version import VERSION

REPO = "Sys-MA/zwift-controller"
API = "https://api.github.com/repos/" + REPO + "/releases/latest"
RELEASES = "https://github.com/" + REPO + "/releases"
ASSET = "ZwiftController.exe"
MAX_EXE_BYTES = 150 * 1024 * 1024

def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else None

def request(url):
    return urlopen(Request(url, headers={"User-Agent": "ZwiftController/" + VERSION}), timeout=20)

def asset_url(asset):
    url = asset.get("browser_download_url", "")
    if not url.startswith(RELEASES + "/download/"):
        raise ValueError("Adresse de telechargement inattendue")
    return url

def select_release(data, current=VERSION):
    tag = data.get("tag_name", "")
    version = version_tuple(tag)
    if data.get("draft") or data.get("prerelease") or version is None or version <= version_tuple(current):
        return None
    assets = {a["name"]: a for a in data.get("assets", [])}
    exe = assets.get(ASSET)
    checksum = assets.get(ASSET + ".sha256")
    if not exe:
        return {"version": tag, "installable": False}
    digest = exe.get("digest") or ""
    sha = digest[7:] if digest.startswith("sha256:") else None
    if sha and not re.fullmatch("[a-fA-F0-9]{64}", sha):
        sha = None
    return {"version": tag, "url": asset_url(exe), "sha256": sha,
            "checksum_url": asset_url(checksum) if checksum and not sha else None,
            "installable": bool(sha or checksum)}

def check():
    try:
        with request(API) as response:
            return select_release(json.loads(response.read(2 * 1024 * 1024)))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise

def file_digest(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def stage(release):
    if not getattr(sys, "frozen", False) or os.name != "nt":
        raise RuntimeError("Installation automatique disponible uniquement avec l'EXE")
    expected = release["sha256"]
    if not expected:
        with request(release["checksum_url"]) as response:
            checksum = response.read(4096).decode("ascii").strip()
        match = re.fullmatch(r"([a-fA-F0-9]{64})(?:\s+\*?ZwiftController\.exe)?", checksum)
        if not match:
            raise ValueError("Somme SHA-256 invalide")
        expected = match[1]
    target = Path(sys.executable).resolve()
    fd, name = tempfile.mkstemp(prefix=".zwift-update-", suffix=".exe", dir=target.parent)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as out, request(release["url"]) as response:
            size = 0
            while chunk := response.read(128 * 1024):
                size += len(chunk)
                if size > MAX_EXE_BYTES:
                    raise ValueError("Fichier de mise a jour trop volumineux")
                out.write(chunk)
        if file_digest(path).lower() != expected.lower():
            raise ValueError("Verification SHA-256 echouee")
        with path.open("rb") as f:
            if f.read(2) != b"MZ":
                raise ValueError("Fichier Windows invalide")
        return path, expected.lower()
    except BaseException:
        path.unlink(missing_ok=True)
        raise

def launch_installer(staged, expected):
    """The external helper waits for GUI shutdown, replaces the EXE and restarts it."""
    target = Path(sys.executable).resolve()
    staged = Path(staged).resolve()
    if staged.parent != target.parent or staged == target or not staged.name.startswith(".zwift-update-"):
        raise ValueError("Chemin de mise a jour invalide")
    if file_digest(staged) != expected:
        raise ValueError("Verification SHA-256 echouee")
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = f"""
$ErrorActionPreference = 'Stop'
$target = {literal(target)}
$staged = {literal(staged)}
$expected = {literal(expected)}
$backup = $target + '.previous'
$log = $target + '.update.log'
$replaced = $false
try {{
    Wait-Process -Id {os.getpid()} -Timeout 90 -ErrorAction SilentlyContinue
    if (Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue) {{ throw 'Application encore ouverte' }}
    if ((Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {{ throw 'SHA256 invalide' }}
    # PyInstaller's parent process may still hold the executable briefly.
    for ($attempt = 0; $attempt -lt 20; $attempt++) {{
        try {{
            [System.IO.File]::Replace($staged, $target, $backup, $true)
            $replaced = $true
            break
        }} catch {{
            if ($attempt -eq 19 -or -not (Test-Path -LiteralPath $staged)) {{ throw }}
            Start-Sleep -Milliseconds 500
        }}
    }}
    Start-Process -FilePath $target
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
}} catch {{
    $_ | Out-File -LiteralPath $log -Append
    if ($replaced) {{
        try {{ [System.IO.File]::Copy($backup, $target, $true) }} catch {{ $_ | Out-File -LiteralPath $log -Append }}
    }}
    if (-not (Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue)) {{
        try {{ Start-Process -FilePath $target }} catch {{ $_ | Out-File -LiteralPath $log -Append }}
    }}
}}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)

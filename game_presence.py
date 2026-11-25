"""Local MyWhoosh detection without launching subprocesses or adding dependencies."""
import ctypes
from ctypes import wintypes
import os
import time

GAME_EXE = "mywhoosh.exe"

def game_running():
    """Return True/False, or None when process enumeration fails."""
    if os.name != "nt":
        return None
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snapshot.restype = wintypes.HANDLE
    first = kernel.Process32FirstW
    next_entry = kernel.Process32NextW
    for function in (first, next_entry):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = snapshot(0x00000002, 0)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not first(handle, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile.casefold() == GAME_EXE:
                return True
            if not next_entry(handle, ctypes.byref(entry)):
                # ERROR_NO_MORE_FILES is the normal end of the snapshot.
                return False if ctypes.get_last_error() == 18 else None
    finally:
        kernel.CloseHandle(handle)

class GamePolicy:
    """Manual choices last until the next observed game open/close transition."""
    def __init__(self, close_delay=10.0):
        self.running = None
        self.override = None
        self.missing_since = None
        self.close_delay = close_delay

    def observe(self, running, now=None):
        now = time.monotonic() if now is None else now
        if running is None:
            # Unknown is not a confirmed absence. Require consecutive absence.
            self.missing_since = None
            return
        if running:
            self.missing_since = None
        elif self.running is True:
            if self.missing_since is None:
                self.missing_since = now
            if now - self.missing_since < self.close_delay:
                return
        if running != self.running:
            if self.running is not None:
                self.override = None
            self.running = running
            self.missing_since = None

    def manual(self, desired):
        self.override = bool(desired)

    @property
    def desired(self):
        return self.override if self.override is not None else self.running is True

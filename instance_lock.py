"""One bridge per Windows session, regardless of entry point."""
import ctypes
import os
from contextlib import contextmanager

class InstanceUnavailable(RuntimeError):
    pass

@contextmanager
def single_instance():
    if os.name != "nt":
        yield
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, r"Local\ZwiftControllerDesktop")
    error = ctypes.get_last_error()
    if not handle or error == 183:
        if handle:
            kernel.CloseHandle(handle)
        raise InstanceUnavailable("Zwift Controller est deja ouvert ou le verrou Windows est inaccessible. Fermer aussi l'icone pres de l'horloge.")
    try:
        yield
    finally:
        kernel.CloseHandle(handle)

"""Windows keyboard output, restricted to the foreground MyWhoosh process."""
import ctypes as C
from ctypes import wintypes as W
import os

# Only the two gear shortcuts have been confirmed in MyWhoosh.
DEFAULT_KEYS = {"PLUS": "K", "MOINS": "I", "GAUCHE": "LEFT",
                "DROITE": "RIGHT", "HAUT": "UP", "BAS": "DOWN",
                "A": "ENTER", "B": "ESC", "Y": "TAB", "Z": "SPACE"}
SPECIAL = {"LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
           "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09, "SPACE": 0x20}
EXTENDED = {0x25, 0x26, 0x27, 0x28}

def key_code(name):
    if name is None or name == "":
        return None
    name = name.upper()
    if name in SPECIAL:
        return SPECIAL[name]
    if len(name) == 1 and name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        return ord(name)
    raise ValueError(f"Unsupported key: {name}")

class Keyboard:
    def __init__(self, log, mapping=None):
        if os.name != "nt":
            raise RuntimeError("Keyboard mode requires Windows")
        self.log = log
        self.mapping = DEFAULT_KEYS.copy()
        if mapping:
            self.mapping.update(mapping)
        self.codes = {button: key_code(key) for button, key in self.mapping.items()}
        self.ready = False
        self.warned = False
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        u, k = self.user, self.kernel
        u.GetForegroundWindow.restype = W.HWND
        u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        u.GetWindowThreadProcessId.restype = W.DWORD
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        k.OpenProcess.restype = W.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        k.QueryFullProcessImageNameW.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]
        u.MapVirtualKeyW.argtypes = [W.UINT, W.UINT]
        u.MapVirtualKeyW.restype = W.UINT

        class KEYBDINPUT(C.Structure):
            _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                        ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]
        class MOUSEINPUT(C.Structure):
            _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                        ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]
        class HARDWAREINPUT(C.Structure):
            _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]
        class INPUTUNION(C.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]
        class INPUT(C.Structure):
            _anonymous_ = ("union",)
            _fields_ = [("type", W.DWORD), ("union", INPUTUNION)]
        self.INPUT = INPUT
        self.KEYBDINPUT = KEYBDINPUT
        u.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
        u.SendInput.restype = W.UINT

    def foreground_is_mywhoosh(self):
        hwnd = self.user.GetForegroundWindow()
        if not hwnd:
            return False
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            buf = C.create_unicode_buffer(32768)
            size = W.DWORD(len(buf))
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
                return False
            return os.path.basename(buf.value).casefold() == "mywhoosh.exe"
        finally:
            self.kernel.CloseHandle(handle)

    async def start(self):
        self.log("CLAVIER | MyWhoosh doit etre au premier plan")
        self.log("CLAVIER | " + ", ".join(f"{b}={k}" for b, k in self.mapping.items()))

    def set_ready(self, ready):
        self.ready = ready

    def set_battery(self, level):
        pass

    def button(self, name, pressed):
        if not self.ready or not pressed:
            return
        vk = self.codes.get(name)
        if vk is None:
            return
        if not self.foreground_is_mywhoosh():
            if not self.warned:
                self.log("CLAVIER | Ignore : MyWhoosh hors premier plan")
                self.warned = True
            return
        self.warned = False
        scan = self.user.MapVirtualKeyW(vk, 0)
        flags = 0x0008 | (0x0001 if vk in EXTENDED else 0)
        events = (self.INPUT * 2)()
        for index in range(2):
            events[index].type = 1
            events[index].ki = self.KEYBDINPUT(0, scan, flags | (2 if index else 0), 0, 0)
        sent = self.user.SendInput(2, events, C.sizeof(self.INPUT))
        if sent != 2:
            if sent == 1:
                self.user.SendInput(1, C.byref(events[1]), C.sizeof(self.INPUT))
            self.log(f"CLAVIER | SendInput incomplet ({sent}/2), erreur {C.get_last_error()}")
        else:
            self.log(f"CLAVIER | {name} -> {self.mapping[name]}")

    async def close(self):
        self.ready = False

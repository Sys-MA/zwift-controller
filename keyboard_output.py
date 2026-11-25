"""Windows keyboard output, restricted to the foreground MyWhoosh process."""
import ctypes as C
from ctypes import wintypes as W
import os
import asyncio

LEGACY_KEYS = {"PLUS": "K", "MOINS": "I", "GAUCHE": "LEFT",
               "DROITE": "RIGHT", "HAUT": "UP", "BAS": "DOWN",
               "A": "ENTER", "B": "ESC", "Y": "TAB", "Z": "SPACE"}
DEFAULT_KEYS = {"PLUS": "K", "MOINS": "I", "GAUCHE": "A",
                "DROITE": "D", "HAUT": "U", "BAS": "C",
                "A": "1", "B": "I", "Y": "2", "Z": "3"}

def migrate_mapping(config):
    old = config.get("keyboard_mapping", {})
    if config.get("keyboard_mapping_version", 0) < 2:
        config["keyboard_mapping"] = {
            button: DEFAULT_KEYS.get(button, key) if key == LEGACY_KEYS.get(button) else key
            for button, key in old.items()
        }
        config["keyboard_mapping_version"] = 2
    return config

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
    def __init__(self, log, mapping=None, modes=None):
        if os.name != "nt":
            raise RuntimeError("Keyboard mode requires Windows")
        self.log = log
        self.mapping = DEFAULT_KEYS.copy()
        if mapping:
            self.mapping.update(mapping)
        self.codes = {button: key_code(key) for button, key in self.mapping.items()}
        self.modes = modes or {}
        self.held = {}
        self.repeats = {}
        self.pressed = set()
        self.monitor = None
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
        self.monitor = asyncio.create_task(self.watch_focus())
        self.log("CLAVIER | MyWhoosh doit etre au premier plan")
        self.log("CLAVIER | " + ", ".join(f"{b}={k}" for b, k in self.mapping.items()))

    def set_ready(self, ready):
        self.ready = ready
        if not ready:
            self.release_all()

    def set_battery(self, level):
        pass

    def send(self, vk, actions):
        scan = self.user.MapVirtualKeyW(vk, 0)
        flags = 0x0008 | (0x0001 if vk in EXTENDED else 0)
        events = (self.INPUT * len(actions))()
        for index, released in enumerate(actions):
            events[index].type = 1
            events[index].ki = self.KEYBDINPUT(0, scan, flags | (2 if released else 0), 0, 0)
        sent = self.user.SendInput(len(actions), events, C.sizeof(self.INPUT))
        if sent != len(actions):
            if len(actions) == 2 and sent == 1:
                self.user.SendInput(1, C.byref(events[1]), C.sizeof(self.INPUT))
            self.log(f"CLAVIER | SendInput incomplet ({sent}/{len(actions)}). Verifier les droits administrateur de MyWhoosh.")
            return False
        return True

    def release(self, name):
        self.pressed.discard(name)
        task = self.repeats.pop(name, None)
        if task:
            task.cancel()
        vk = self.held.pop(name, None)
        if vk is not None and vk not in self.held.values():
            self.send(vk, [True])

    def release_all(self):
        for name in list(self.pressed | self.held.keys() | self.repeats.keys()):
            self.release(name)

    async def watch_focus(self):
        previous = None
        while True:
            focused = self.foreground_is_mywhoosh()
            if focused != previous:
                self.log("CLAVIER | FOCUS | " + ("MyWhoosh actif" if focused else "MyWhoosh hors premier plan"))
                previous = focused
            if not focused:
                self.release_all()
            await asyncio.sleep(0.25)

    async def repeat(self, name, vk):
        await asyncio.sleep(0.45)
        while name in self.pressed and self.ready and self.foreground_is_mywhoosh():
            if vk not in self.held.values():
                self.send(vk, [False, True])
            await asyncio.sleep(0.15)

    def button(self, name, pressed):
        if not pressed:
            self.release(name)
            return
        if not self.ready or name in self.pressed:
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
        self.pressed.add(name)
        mode = self.modes.get(name, "Simple")
        if mode == "Maintenue":
            if vk in self.held.values() or self.send(vk, [False]):
                self.held[name] = vk
        elif vk not in self.held.values():
            self.send(vk, [False, True])
            if mode == "Repetition":
                self.repeats[name] = asyncio.create_task(self.repeat(name, vk))
        self.log(f"CLAVIER | {name} -> {self.mapping[name]}")

    async def close(self):
        self.ready = False
        tasks = list(self.repeats.values())
        self.release_all()
        if self.monitor:
            self.monitor.cancel()
            tasks.append(self.monitor)
        await asyncio.gather(*tasks, return_exceptions=True)

import asyncio
import argparse
import json
import os
from pathlib import Path
import time
from datetime import datetime
from bleak import BleakScanner, BleakClient

ZWIFT_ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_SYNC_RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_SYNC_TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
HANDSHAKE = [bytes.fromhex(x) for x in (
    "52 69 64 65 4F 6E 02 03", "00 08 00", "00 08 10")]
BUTTONS = {
    0x0001: "GAUCHE", 0x0002: "HAUT", 0x0004: "DROITE",
    0x0008: "BAS", 0x0010: "A", 0x0020: "B",
    0x0040: "Y", 0x0080: "Z", 0x0100: "MOINS", 0x1000: "PLUS"
}
LEFT_MASK = 0x010F
RIGHT_MASK = 0x10F0

def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}", flush=True)

def decode_buttons(data):
    if len(data) < 3 or data[:2] != bytes([0x23, 0x08]):
        return None
    value = 0
    for index, byte in enumerate(data[2:7]):
        if index == 4 and byte > 15:
            return None
        value |= (byte & 127) << (index * 7)
        if not byte & 128:
            return (~value) & 0xFFFFFFFF
    return None

def changes(previous, current):
    return [(name, bool(current & mask), mask)
            for mask, name in BUTTONS.items()
            if (previous ^ current) & mask]

CONFIG = Path(__file__).with_name("config.json")
# Known pair from the v0.4 hardware test; override for another pair.
DEFAULTS = {"left": "C8:54:6B:F3:6B:A7", "right": "C8:54:6B:08:A9:C2",
            "battery_interval": 900}

def load_config():
    config = DEFAULTS.copy()
    if CONFIG.exists():
        config.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return config

def disable_quickedit():
    if os.name != "nt":
        return lambda: None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.GetStdHandle(-10 & 0xFFFFFFFF)
    mode = wintypes.DWORD()
    if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        original = mode.value
        if kernel.SetConsoleMode(handle, (original | 0x80) & ~0x40):
            return lambda: kernel.SetConsoleMode(handle, original)
    return lambda: None

async def bounded(awaitable, seconds=10):
    return await asyncio.wait_for(awaitable, timeout=seconds)

async def disconnect(client):
    try:
        await bounded(client.disconnect(), 5)
    except Exception as exc:
        log(f"Deconnexion : {type(exc).__name__}: {exc!r}")

async def find_device(address):
    return await bounded(BleakScanner.find_device_by_address(address, timeout=5), 8)

async def read_battery(client, side):
    try:
        data = await bounded(client.read_gatt_char(BATTERY), 5)
        if len(data) == 1 and data[0] <= 100:
            log(f"{side} | Batterie : {data[0]}%")
            return True
        log(f"{side} | Batterie : valeur invalide")
    except Exception as exc:
        log(f"{side} | Batterie indisponible : {type(exc).__name__}: {exc!r}")
    return False

async def right_battery(address):
    client = None
    try:
        device = await find_device(address)
        if device is None:
            log("DROITE | Non detectee pour la batterie")
            return False
        client = BleakClient(device, timeout=10)
        log("DROITE | Connexion temporaire pour la batterie")
        await bounded(client.connect(), 15)
        return await read_battery(client, "DROITE")
    except Exception as exc:
        log(f"DROITE | {type(exc).__name__}: {exc!r}")
        return False
    finally:
        if client is not None:
            await disconnect(client)
            log("DROITE | Fin de connexion temporaire")

async def left_session(device, config, battery_enabled):
    client = BleakClient(device, timeout=15)
    state = 0
    battery_task = None
    def emit(current):
        nonlocal state
        for button, pressed, mask in changes(state, current):
            log(f"GAUCHE | {button} | {'ENFONCE' if pressed else 'RELACHE'} | 0x{mask:04X}")
        state = current
    def notify(sender, data):
        current = decode_buttons(data)
        if current is not None:
            emit(current)
    async def batteries():
        while client.is_connected:
            success = await right_battery(config["right"])
            await asyncio.sleep(config["battery_interval"] if success else 60)
    try:
        log("GAUCHE | Connexion...")
        await bounded(client.connect(), 20)
        await bounded(client.start_notify(ZWIFT_ASYNC, notify))
        await bounded(client.start_notify(ZWIFT_SYNC_TX, lambda s, d: None))
        char = client.services.get_characteristic(ZWIFT_SYNC_RX)
        if char is None:
            raise RuntimeError("Sync RX introuvable")
        response = "write" in char.properties
        for payload in HANDSHAKE:
            await bounded(client.write_gatt_char(char, payload, response=response))
            await asyncio.sleep(0.3)
        log("GAUCHE | Initialisation terminee - 10 boutons")
        await read_battery(client, "GAUCHE")
        if battery_enabled:
            battery_task = asyncio.create_task(batteries())
        next_battery = time.monotonic() + config["battery_interval"]
        while client.is_connected:
            await asyncio.sleep(3)
            if not client.is_connected:
                break
            await bounded(client.write_gatt_char(char, b"RideOn", response=response))
            if time.monotonic() >= next_battery:
                await read_battery(client, "GAUCHE")
                next_battery = time.monotonic() + config["battery_interval"]
    finally:
        if battery_task is not None:
            battery_task.cancel()
            await asyncio.gather(battery_task, return_exceptions=True)
        emit(0)
        await disconnect(client)
        log("GAUCHE | Deconnectee")

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-right-battery", action="store_true")
    parser.add_argument("--discover", action="store_true",
                        help="List nearby Zwift Click addresses")
    args = parser.parse_args()
    if args.discover:
        for device, adv in (await BleakScanner.discover(timeout=8, return_adv=True)).values():
            if "zwift click" in (adv.local_name or device.name or "").casefold():
                log(f"Zwift Click : {device.address}")
        return
    config = load_config()
    if config["left"].upper() == config["right"].upper() or config["battery_interval"] < 60:
        raise ValueError("Configuration invalide")
    print("MyWhoosh Controller - v0.4.1")
    print("Connexion principale gauche. CTRL+C pour quitter.")
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
    while True:
        try:
            device = await find_device(config["left"])
            if device is not None:
                await left_session(device, config, not args.no_right_battery)
            else:
                log("GAUCHE | En attente de la manette")
        except Exception as exc:
            log(f"GAUCHE | {type(exc).__name__}: {exc!r}")
        await asyncio.sleep(5)

if __name__ == "__main__":
    restore_console = disable_quickedit()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgramme arrete.")
    finally:
        restore_console()

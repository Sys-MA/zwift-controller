import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
import time
from datetime import datetime
from bleak import BleakScanner, BleakClient
from instance_lock import single_instance, InstanceUnavailable
from version import VERSION
from zwift_auth import Session, AuthError, challenge_payload

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

DEBUG_FILE = None

def rotate_debug():
    global DEBUG_FILE
    path = Path(DEBUG_FILE.name)
    DEBUG_FILE.close()
    try:
        path.replace(path.with_suffix(".log.1"))
    finally:
        DEBUG_FILE = path.open("a", encoding="utf-8")

def debug(message):
    if DEBUG_FILE is not None:
        log(message)

def log(message):
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}"
    if sys.stdout is not None:
        print(line, flush=True)
    if DEBUG_FILE is not None:
        if DEBUG_FILE.tell() >= 5 * 1024 * 1024:
            rotate_debug()
        DEBUG_FILE.write(line + "\n")
        DEBUG_FILE.flush()

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
    from keyboard_output import migrate_mapping
    return migrate_mapping(config)

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

async def read_battery(client, side, bridge=None):
    try:
        data = await bounded(client.read_gatt_char(BATTERY), 5)
        if len(data) == 1 and data[0] <= 100:
            log(f"{side} | Batterie : {data[0]}%")
            if bridge is not None:
                bridge.set_battery(data[0])
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

async def left_session(device, config, battery_enabled, bridge=None, recovery=None, reset_event=None):
    client = BleakClient(device, timeout=15)
    state = 0
    battery_task = None
    status_task = None
    counts = {"async": 0, "sync": 0, "decoded": 0, "keepalive": 0}
    last_rx = None
    last_button_frame = time.monotonic()
    challenge = False
    started = time.monotonic()
    def received(channel, data):
        nonlocal last_rx
        counts[channel] += 1
        last_rx = time.monotonic()
        debug(f"RX {channel.upper()} #{counts[channel]} | {bytes(data).hex(' ')}")
    def sync_notify(sender, data):
        received("sync", data)
    async def status():
        while True:
            await asyncio.sleep(10)
            age = "jamais" if last_rx is None else f"{time.monotonic()-last_rx:.1f}s"
            debug(f"STATUS | connected={client.is_connected} | session={time.monotonic()-started:.1f}s | RX_ASYNC={counts['async']} | RX_SYNC={counts['sync']} | decoded={counts['decoded']} | keepalive={counts['keepalive']} | last_rx={age}")
    def emit(current):
        nonlocal state
        for button, pressed, mask in changes(state, current):
            log(f"GAUCHE | {button} | {'ENFONCE' if pressed else 'RELACHE'} | 0x{mask:04X}")
            if bridge is not None:
                bridge.button(button, pressed)
        state = current
    def notify(sender, data):
        nonlocal last_button_frame, challenge
        received("async", data)
        if bytes(data[:2]) == b"\xff\x03":
            challenge = True
            log("GAUCHE | Challenge authentification Zwift recu (FF 03)")
        current = decode_buttons(data)
        if current is not None:
            counts["decoded"] += 1
            last_button_frame = time.monotonic()
            if recovery is not None:
                recovery["failures"] = 0
            emit(current)
        else:
            debug("RX ASYNC | message non decode comme bouton")
    async def batteries():
        while client.is_connected:
            success = await right_battery(config["right"])
            await asyncio.sleep(config["battery_interval"] if success else 60)
    try:
        log("GAUCHE | Connexion...")
        await bounded(client.connect(), 20)
        debug("BLE | Connexion GATT etablie")
        if DEBUG_FILE is not None:
            status_task = asyncio.create_task(status())
        await bounded(client.start_notify(ZWIFT_ASYNC, notify))
        await bounded(client.start_notify(ZWIFT_SYNC_TX, sync_notify))
        debug("BLE | Abonnements ASYNC et SYNC_TX actifs")
        char = client.services.get_characteristic(ZWIFT_SYNC_RX)
        if char is None:
            raise RuntimeError("Sync RX introuvable")
        response = "write" in char.properties
        debug(f"BLE | Sync RX properties={char.properties} | response={response}")
        for payload in HANDSHAKE:
            debug(f"TX HANDSHAKE | {payload.hex(' ')}")
            await bounded(client.write_gatt_char(char, payload, response=response))
            debug("TX HANDSHAKE | OK")
            await asyncio.sleep(0.3)
        log("GAUCHE | Initialisation terminee - 10 boutons")
        if bridge is not None:
            bridge.set_ready(True)
        await read_battery(client, "GAUCHE", bridge)
        if battery_enabled:
            battery_task = asyncio.create_task(batteries())
        last_button_frame = time.monotonic()
        next_battery = time.monotonic() + config["battery_interval"]
        while client.is_connected:
            await asyncio.sleep(3)
            if not client.is_connected:
                break
            now = time.monotonic()
            manual = reset_event is not None and reset_event.is_set()
            auto = (recovery is not None and now - last_button_frame >= 55
                    and now >= recovery["next_allowed"])
            if manual or auto:
                if manual:
                    reset_event.clear()
                if recovery is not None:
                    recovery["failures"] += 1
                    recovery["next_allowed"] = now + (300 if recovery["failures"] >= 3 else 60)
                if battery_task is not None:
                    battery_task.cancel()
                    await asyncio.gather(battery_task, return_exceptions=True)
                    battery_task = None
                log(f"RESET | GAUCHE | {'manuel' if manual else 'absence de trames boutons depuis 55s'} | TX 18 | challenge={challenge}")
                await bounded(client.write_gatt_char(char, bytes([0x18]), response=response), 5)
                log("RESET | Commande envoyee, reconnexion apres 5s")
                await asyncio.sleep(1)
                break
            debug("TX KEEPALIVE | RideOn")
            await bounded(client.write_gatt_char(char, b"RideOn", response=response))
            counts["keepalive"] += 1
            debug(f"KEEPALIVE | OK #{counts['keepalive']}")
            if time.monotonic() >= next_battery:
                await read_battery(client, "GAUCHE")
                next_battery = time.monotonic() + config["battery_interval"]
    finally:
        if status_task is not None:
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
        debug(f"STATUS FINAL | {counts}")
        if battery_task is not None:
            battery_task.cancel()
            await asyncio.gather(battery_task, return_exceptions=True)
        emit(0)
        if bridge is not None:
            bridge.set_ready(False)
        await disconnect(client)
        log("GAUCHE | Deconnectee")

async def dual_sessions(config, bridge, read_right=True, auto_reset=True, reset_after=45.0, refresh_handshake=False, fast_reconnect=False, reset_event=None, auth_client=None, auth_reconnect=None):
    scan_lock = asyncio.Lock()
    ready = set()

    async def worker(side, address, mask):
        cached_device = None
        recovery_started = None
        failures = 0
        next_reset_allowed = 0.0
        while True:
            client = None
            reboot = False
            accepting_frames = True
            connected_at = None
            use_cache = False
            device = None
            initialized = False
            cache_failed = False
            last_rx = time.monotonic()
            last_frame = last_rx
            health = None
            challenges = 0
            pending_challenge = None
            active_challenge = None
            solve_task = None
            signed_sent_at = None
            auth_confirmed = False
            state = 0
            counts = {"rx": 0, "decoded": 0, "sync": 0}
            def emit(current):
                nonlocal state
                current &= mask
                for button, pressed, bit in changes(state, current):
                    log(f"{side} | {button} | {'ENFONCE' if pressed else 'RELACHE'} | 0x{bit:04X}")
                    if bridge is not None:
                        bridge.button(button, pressed)
                state = current
            def notify(sender, data):
                nonlocal challenges, last_rx, last_frame, pending_challenge, signed_sent_at, auth_confirmed
                if not accepting_frames:
                    return
                last_rx = time.monotonic()
                counts["rx"] += 1
                if bytes(data[:2]) == b"\xff\x03":
                    challenges += 1
                    log(f"{side} | Challenge authentification Zwift recu (FF 03)")
                    payload = challenge_payload(data)
                    if side == "GAUCHE" and auth_client is not None and auth_client.ready and payload:
                        if payload != active_challenge:
                            pending_challenge = payload
                        signed_sent_at = None
                        auth_confirmed = False
                    elif challenges == 2:
                        log(f"{side} | AUTH | Renouvellement Zwift recommande")
                else:
                    debug(f"{side} | RX ASYNC #{counts['rx']} | {bytes(data).hex(' ')}")
                current = decode_buttons(data)
                if current is not None and accepting_frames:
                    counts["decoded"] += 1
                    last_frame = time.monotonic()
                    if signed_sent_at is not None and not auth_confirmed:
                        auth_confirmed = True
                        challenges = 0
                        log(f"{side} | AUTH_OK | Trame boutons recue apres reponse signee")
                    emit(current)
            def sync_notify(sender, data):
                nonlocal last_rx
                if not accepting_frames:
                    return
                last_rx = time.monotonic()
                counts["sync"] += 1
                debug(f"{side} | RX SYNC #{counts['sync']} | {bytes(data).hex(' ')}")
            try:
                device = cached_device
                cached_device = None
                use_cache = fast_reconnect and device is not None and os.name == "nt"
                if device is None:
                    async with scan_lock:
                        device = await find_device(address)
                if device is not None:
                    options = {"winrt": {"use_cached_services": True}} if use_cache else {}
                    client = BleakClient(device, timeout=15, **options)
                    connect_started = time.monotonic()
                    log(f"{side} | Connexion... | cache GATT={use_cache}")
                    await bounded(client.connect(), 8 if recovery_started is not None else 20)
                    connected_at = time.monotonic()
                    log(f"{side} | TIMING | GATT connecte en {connected_at-connect_started:.3f}s")
                if client is None:
                    log(f"{side} | En attente de la manette")
                else:
                    subscribe_started = time.monotonic()
                    await bounded(client.start_notify(ZWIFT_ASYNC, notify))
                    await bounded(client.start_notify(ZWIFT_SYNC_TX, sync_notify))
                    log(f"{side} | TIMING | abonnements en {time.monotonic()-subscribe_started:.3f}s")
                    char = client.services.get_characteristic(ZWIFT_SYNC_RX)
                    if char is None:
                        raise RuntimeError("Sync RX introuvable")
                    response = "write" in char.properties
                    handshake_started = time.monotonic()
                    for index, payload in enumerate(HANDSHAKE):
                        debug(f"{side} | TX HANDSHAKE | {payload.hex(' ')}")
                        await bounded(client.write_gatt_char(char, payload, response=response))
                        if index < len(HANDSHAKE) - 1:
                            await asyncio.sleep(0.3)
                    log(f"{side} | TIMING | handshake en {time.monotonic()-handshake_started:.3f}s")
                    initialized = True
                    ready.add(side)
                    if bridge is not None:
                        bridge.set_ready(True)
                    log(f"{side} | Initialisation terminee - 5 boutons")
                    if recovery_started is not None:
                        log(f"{side} | RECOVERY | initialisation en {time.monotonic()-recovery_started:.2f}s")
                        recovery_started = None
                        failures = 0
                    if side == "GAUCHE" or read_right:
                        await read_battery(client, side, bridge if side == "GAUCHE" else None)
                    next_battery = time.monotonic() + config["battery_interval"]
                    started = time.monotonic()
                    next_status = started
                    next_keepalive = started + 3
                    next_refresh = connected_at + reset_after
                    while client.is_connected:
                        await asyncio.sleep(0.1)
                        if not client.is_connected:
                            break
                        now = time.monotonic()
                        if side == "GAUCHE" and auth_reconnect is not None and auth_reconnect.is_set():
                            auth_reconnect.clear()
                            log(f"{side} | Reconnexion pour actualiser la session Zwift")
                            break
                        if solve_task is not None and solve_task.done():
                            try:
                                signed = solve_task.result()
                                if pending_challenge is None and auth_client.ready:
                                    await bounded(client.write_gatt_char(char, signed, response=response), 5)
                                    signed_sent_at = time.monotonic()
                                    log(f"{side} | AUTH_SENT | Reponse signee transmise")
                            except AuthError as exc:
                                log(f"{side} | AUTH | {exc}")
                            except Exception:
                                log(f"{side} | AUTH | Envoi de la reponse impossible")
                            finally:
                                solve_task = None
                                active_challenge = None
                        if pending_challenge is not None and solve_task is None:
                            if auth_client is not None and auth_client.ready:
                                active_challenge, pending_challenge = pending_challenge, None
                                solve_task = asyncio.create_task(asyncio.to_thread(auth_client.solve, active_challenge))
                            else:
                                pending_challenge = None
                        if signed_sent_at is not None and not auth_confirmed and now - signed_sent_at > 20:
                            signed_sent_at = None
                            log(f"{side} | AUTH | Reponse envoyee, aucune trame boutons recue")
                        # A lack of button frames alone is not proof of failure.
                        new_health = ("silent" if now-last_rx >= 20 else
                                      "suspect" if challenges >= 2 and now-last_frame >= 60 else "ok")
                        if new_health != health:
                            health = new_health
                            log(f"{side} | HEALTH | {health}")
                        if side == "GAUCHE" and refresh_handshake and now >= next_refresh:
                            log(f"{side} | REFRESH | debut sans deconnexion | decoded={counts['decoded']} | challenges={challenges}")
                            refresh_started = time.monotonic()
                            for payload in HANDSHAKE:
                                debug(f"{side} | TX REFRESH | {payload.hex(' ')}")
                                await bounded(client.write_gatt_char(char, payload, response=response))
                                await asyncio.sleep(0.3)
                            log(f"{side} | REFRESH | termine en {time.monotonic()-refresh_started:.2f}s | connected={client.is_connected} | decoded={counts['decoded']}")
                            next_refresh = time.monotonic() + reset_after
                            now = time.monotonic()
                        manual = side == "GAUCHE" and reset_event is not None and reset_event.is_set()
                        automatic = (side == "GAUCHE" and auto_reset and not refresh_handshake
                                     and challenges >= 2 and now - connected_at >= reset_after
                                     and not (auth_client is not None and auth_client.ready)
                                     and now >= next_reset_allowed)
                        if manual or automatic:
                            if manual:
                                reset_event.clear()
                            next_reset_allowed = now + 60
                            accepting_frames = False
                            emit(0)
                            recovery_started = time.monotonic()
                            failures += 1
                            log(f"{side} | RESET | {'manuel' if manual else 'preventif'} apres {now-connected_at:.2f}s | TX 18")
                            await bounded(client.write_gatt_char(char, bytes([0x18]), response=response), 5)
                            reboot = True
                            cached_device = device
                            await asyncio.sleep(0.10 if fast_reconnect else 0.25)
                            break
                        if now >= next_keepalive:
                            await bounded(client.write_gatt_char(char, b"RideOn", response=response))
                            debug(f"{side} | KEEPALIVE | OK")
                            next_keepalive = now + 3
                        if now >= next_status:
                            debug(f"{side} | STATUS | connected=True | session={now-started:.1f}s | {counts}")
                            next_status = now + 10
                        if now >= next_battery:
                            if side == "GAUCHE" or read_right:
                                await read_battery(client, side)
                            next_battery = now + config["battery_interval"]
            except Exception as exc:
                log(f"{side} | {type(exc).__name__}: {exc!r}")
                if use_cache:
                    cache_failed = True
                    cached_device = None
                    log(f"{side} | CACHE | echec, prochaine connexion sans cache")
            finally:
                accepting_frames = False
                if solve_task is not None:
                    solve_task.cancel()
                    await asyncio.gather(solve_task, return_exceptions=True)
                if initialized and recovery_started is None:
                    recovery_started = time.monotonic()
                emit(0)
                ready.discard(side)
                if bridge is not None:
                    bridge.set_ready(bool(ready))
                if client is not None:
                    disconnect_started = time.monotonic()
                    await disconnect(client)
                    log(f"{side} | TIMING | deconnexion en {time.monotonic()-disconnect_started:.3f}s")
                    log(f"{side} | Deconnectee | {counts}")
            if fast_reconnect and initialized and not reboot and not cache_failed and device is not None:
                cached_device = device
            await asyncio.sleep((0.10 if fast_reconnect else 0.25) if reboot else 0.15 if cached_device is not None else 0.5 if recovery_started is not None else 5)

    tasks = [
        asyncio.create_task(worker("GAUCHE", config["left"], LEFT_MASK)),
        asyncio.create_task(worker("DROITE", config["right"], RIGHT_MASK)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main(argv=None, reset_event=None, auth_client=None, auth_reconnect=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dual", action="store_true", help="Independent left and right BLE connections")
    parser.add_argument("--fast-reconnect", action="store_true", help="Experimental Windows GATT cache on dual-mode recovery")
    parser.add_argument("--refresh-handshake", action="store_true", help="Experimental in-place handshake refresh in dual mode, without preventive reboot")
    parser.add_argument("--reset-after", type=float, default=45.0, help="Dual mode preventive left reboot delay in seconds (25-48)")
    parser.add_argument("--no-auto-reset", action="store_true", help="Disable experimental idle recovery")
    parser.add_argument("--debug", action="store_true", help="Log raw Bluetooth traffic to debug.log")
    parser.add_argument("--no-right-battery", action="store_true")
    parser.add_argument("--discover", action="store_true",
                        help="List nearby Zwift Click addresses")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-obc", action="store_true", help="Bluetooth only")
    mode.add_argument("--obc", action="store_true", help="Experimental OpenBikeControl")
    mode.add_argument("--keyboard", action="store_true", help="Send keys to foreground MyWhoosh")
    parser.add_argument("--obc-host", help="Local IPv4 address for discovery")
    args = parser.parse_args(argv)
    if args.fast_reconnect and not args.dual:
        parser.error("--fast-reconnect requires --dual")
    if args.refresh_handshake and not args.dual:
        parser.error("--refresh-handshake requires --dual")
    if not 25 <= args.reset_after <= 48:
        parser.error("--reset-after must be between 25 and 48 seconds")
    if args.discover:
        for device, adv in (await BleakScanner.discover(timeout=8, return_adv=True)).values():
            if "zwift click" in (adv.local_name or device.name or "").casefold():
                log(f"Zwift Click : {device.address}")
        return
    config = load_config()
    if config["left"].upper() == config["right"].upper() or config["battery_interval"] < 60:
        raise ValueError("Configuration invalide")
    log(f"MyWhoosh Controller - v{VERSION}")
    log("Mode dual : deux connexions." if args.dual else "Connexion principale gauche. CTRL+C pour quitter.")
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
    global DEBUG_FILE
    if args.debug:
        DEBUG_FILE = CONFIG.with_name("debug.log").open("a", encoding="utf-8")
    recovery = None if args.no_auto_reset else {"failures": 0, "next_allowed": 0.0}
    bridge = None
    try:
        debug(f"DEBUG | session {datetime.now().isoformat()} | right_battery={not args.no_right_battery}")
        if not args.obc and not args.no_obc:
            from keyboard_output import Keyboard
            bridge = Keyboard(log, config.get("keyboard_mapping"), config.get("keyboard_modes"))
            await bridge.start()
        elif args.obc:
            from openbikecontrol import Bridge
            bridge = Bridge(log, host=args.obc_host)
            await bridge.start()
        if args.dual:
            if auth_client is None:
                auth_client = Session()
            await dual_sessions(config, bridge, not args.no_right_battery, not args.no_auto_reset, args.reset_after, args.refresh_handshake, args.fast_reconnect, reset_event, auth_client, auth_reconnect)
            return
        while True:
            try:
                device = await find_device(config["left"])
                if device is not None:
                    await left_session(device, config, not args.no_right_battery, bridge, recovery, reset_event)
                else:
                    log("GAUCHE | En attente de la manette")
            except Exception as exc:
                log(f"GAUCHE | {type(exc).__name__}: {exc!r}")
            await asyncio.sleep(5)
    finally:
        try:
            if bridge is not None:
                await bridge.close()
        finally:
            if DEBUG_FILE is not None:
                DEBUG_FILE.close()
                DEBUG_FILE = None

if __name__ == "__main__":
    restore_console = disable_quickedit()
    try:
        with single_instance():
            asyncio.run(main())
    except InstanceUnavailable as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nProgramme arrete.")
    finally:
        restore_console()

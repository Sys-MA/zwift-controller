"""Experimental Fitbit BLE probe. No pairing, proprietary writes or cloud calls."""
import argparse
import asyncio
from pathlib import Path
from datetime import datetime
from bleak import BleakScanner, BleakClient

HEART_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
FITBIT_COMPANY = 0x018E
FITBIT_SERVICE = "adabfb00-6e7d-4601-bda2-bffaa68956ba"
LOG = Path("fitbit-probe.log")

def log(message):
    line = f"[{datetime.now():%H:%M:%S}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as out:
        out.write(line + "\n")

def candidate(device, advertisement):
    name = (advertisement.local_name or device.name or "").casefold()
    return ("fitbit" in name or "sense" in name or
            FITBIT_COMPANY in advertisement.manufacturer_data or
            FITBIT_SERVICE in advertisement.service_uuids)

def decode_heart_rate(data):
    if not data:
        return None
    size = 2 if data[0] & 1 else 1
    if len(data) < size + 1:
        return None
    return int.from_bytes(data[1:1+size], "little")

async def observe_vendor(client, seconds):
    """Observe only selected notify/read channels; no application commands."""
    suffix = "-e56a-484c-b832-8b17cf6cbfe8"
    allowed = ("abbafd01", "abbafd02", "abbafd04")
    subscriptions = []
    counts = {}
    lengths = {}

    def receive(sender, data):
        name = sender.uuid[:8]
        counts[name] = counts.get(name, 0) + 1
        lengths.setdefault(name, set()).add(len(data))
        # Unknown payloads may contain private information. Record metadata only.
        if counts[name] <= 5:
            log(f"  Notification {name}: {len(data)} bytes")

    try:
        for prefix in allowed:
            char = client.services.get_characteristic(prefix + suffix)
            if char is None:
                log(f"{prefix}: absent")
                continue
            if "read" in char.properties:
                try:
                    data = await asyncio.wait_for(client.read_gatt_char(char), timeout=5)
                    log(f"{prefix}: read succeeded, {len(data)} bytes (content omitted)")
                except Exception as exc:
                    log(f"{prefix}: read failed ({type(exc).__name__})")
            if "notify" in char.properties or "indicate" in char.properties:
                try:
                    await asyncio.wait_for(client.start_notify(char, receive), timeout=5)
                    subscriptions.append(char)
                    log(f"{prefix}: subscribed")
                except Exception as exc:
                    log(f"{prefix}: subscription failed ({type(exc).__name__})")
        if subscriptions:
            log(f"Observing for {seconds} seconds; keep an exercise running on the watch.")
            await asyncio.sleep(seconds)
        for char in subscriptions:
            name = char.uuid[:8]
            log(f"{name}: {counts.get(name, 0)} notifications; packet sizes={sorted(lengths.get(name, set()))}")
    finally:
        for char in subscriptions:
            if client.is_connected:
                try:
                    await asyncio.wait_for(client.stop_notify(char), timeout=5)
                except Exception:
                    pass

async def run(args):
    LOG.write_text("", encoding="utf-8")
    log("Fitbit probe: " + ("scan + inspection" if args.address else "scan only"))
    log(f"Scanning for {args.scan_seconds} seconds...")
    seen = await BleakScanner.discover(timeout=args.scan_seconds, return_adv=True)
    matches = []
    for device, adv in seen.values():
        selected = args.address and device.address.casefold() == args.address.casefold()
        if selected or candidate(device, adv) or HEART_SERVICE in adv.service_uuids:
            matches.append(device)
            log(f"Device: {device.address} | name={adv.local_name or device.name or '(unnamed)'} | RSSI={adv.rssi}")
            log("  Advertised services: " + (", ".join(adv.service_uuids) or "(none)"))
            log("  Manufacturer IDs: " + (", ".join(f"0x{k:04x}" for k in adv.manufacturer_data) or "(none)"))
    log(f"Total nearby BLE devices: {len(seen)}; matching candidates: {len(matches)}")
    if not args.address:
        if not matches:
            log("No Fitbit candidate. Wake the watch and repeat the scan.")
        else:
            log("Use --address with the confirmed address of your own watch to inspect its GATT services.")
        return
    selected = next((device for device in matches if device.address.casefold() == args.address.casefold()), None)
    if selected is None:
        log("Selected device was not seen. No connection attempted.")
        return
    log("Connecting without requesting pairing. Decline any unexpected pairing prompt.")
    try:
        async with BleakClient(selected, timeout=12) as client:
            log(f"Connected: {client.is_connected}")
            measurement = None
            heart_service_found = False
            for service in client.services:
                log(f"Service {service.uuid} | {service.description}")
                if service.uuid.lower() == HEART_SERVICE:
                    heart_service_found = True
                for char in service.characteristics:
                    log(f"  Characteristic {char.uuid} | {', '.join(char.properties)} | {char.description}")
                    if service.uuid.lower() == HEART_SERVICE and char.uuid.lower() == HEART_MEASUREMENT:
                        measurement = char
            log(f"Standard Heart Rate Service present: {heart_service_found}")
            if args.observe_vendor:
                await observe_vendor(client, args.observe_vendor)
                return
            if measurement is None:
                log("No standard heart rate measurement characteristic. Proprietary services will not be accessed.")
                return
            log("Standard Heart Rate Measurement found.")
            if not args.listen:
                log("Use --listen to request standard heart rate notifications explicitly.")
                return
            if "notify" not in measurement.properties:
                log("Measurement does not support notifications.")
                return
            received = 0
            def on_heart_rate(sender, data):
                nonlocal received
                bpm = decode_heart_rate(data)
                if bpm is not None:
                    received += 1
                    # Do not store personal heart rate measurements in the diagnostic file.
                    print(f"Heart rate: {bpm} BPM", flush=True)
            await client.start_notify(measurement, on_heart_rate)
            try:
                await asyncio.sleep(args.listen)
            finally:
                if client.is_connected:
                    await client.stop_notify(measurement)
            log(f"Heart rate notifications received: {received} (BPM not saved)")
    except Exception as exc:
        log(f"Inspection failed: {type(exc).__name__}: {exc}")
        log("Do not unpair or reset the watch. A phone connection may prevent another BLE connection.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="Explicit BLE address of your own watch")
    parser.add_argument("--scan-seconds", type=int, default=20, choices=range(5, 61), metavar="5-60")
    parser.add_argument("--listen", type=int, default=0, choices=range(0, 121), metavar="0-120",
                        help="Optional standard HR notification test, seconds (requires --address)")
    parser.add_argument("--observe-vendor", type=int, default=0, choices=range(0, 121), metavar="0-120",
                        help="Explicitly read/subscribe to three observed Fitbit characteristics; log lengths only")
    args = parser.parse_args()
    if (args.listen or args.observe_vendor) and not args.address:
        parser.error("--listen and --observe-vendor require --address")
    if args.listen and args.observe_vendor:
        parser.error("Choose only one observation mode")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log("Stopped by user")
    print(f"Diagnostic file: {LOG.resolve()}")

if __name__ == "__main__":
    main()

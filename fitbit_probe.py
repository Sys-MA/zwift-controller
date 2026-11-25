"""Experimental Fitbit BLE probe. No pairing, proprietary writes or cloud calls."""
import argparse
import asyncio
from pathlib import Path
from datetime import datetime
from bleak import BleakScanner, BleakClient

HEART_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
FITBIT_COMPANY = 0x0224
LOG = Path("fitbit-probe.log")

def log(message):
    line = f"[{datetime.now():%H:%M:%S}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as out:
        out.write(line + "\n")

def candidate(device, advertisement):
    name = (advertisement.local_name or device.name or "").casefold()
    return ("fitbit" in name or "sense" in name or
            FITBIT_COMPANY in advertisement.manufacturer_data)

def decode_heart_rate(data):
    if not data:
        return None
    size = 2 if data[0] & 1 else 1
    if len(data) < size + 1:
        return None
    return int.from_bytes(data[1:1+size], "little")

async def run(args):
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
    args = parser.parse_args()
    if args.listen and not args.address:
        parser.error("--listen requires --address")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log("Stopped by user")
    print(f"Diagnostic file: {LOG.resolve()}")

if __name__ == "__main__":
    main()

"""Passive Fitbit Airlink experiment. No protocol commands or pairing requests."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
from pathlib import Path
import time

from bleak import BleakClient, BleakScanner

RX = "abbaff02-e56a-484c-b832-8b17cf6cbfe8"
DEVICE_INFO = "0000180a-0000-1000-8000-00805f9b34fb"
LOG = Path("fitbit-probe.log")

def log(message):
    line = f"[{datetime.now():%H:%M:%S}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as out:
        out.write(line + "\n")

async def observe(client, seconds):
    rx = client.services.get_characteristic(RX)
    if rx is None or "notify" not in rx.properties:
        log("Airlink RX characteristic unavailable.")
        return
    info = client.services.get_service(DEVICE_INFO)
    if info:
        for prefix, name in (("00002a24", "Model"), ("00002a26", "Firmware"), ("00002a28", "Software")):
            char = next((c for c in info.characteristics if c.uuid.startswith(prefix)), None)
            if char and "read" in char.properties:
                try:
                    raw = await asyncio.wait_for(client.read_gatt_char(char), 5)
                    value = raw.decode("utf-8", errors="replace")
                    log(f"{name}: {value[:100]!r}")
                except Exception as exc:
                    log(f"{name}: unavailable ({type(exc).__name__})")
    sizes = Counter()
    singles = Counter()
    previous = None
    start = time.monotonic()

    def received(sender, data):
        nonlocal previous
        now = time.monotonic()
        gap = "-" if previous is None else f"{now - previous:.3f}s"
        previous = now
        sizes[len(data)] += 1
        if len(data) == 1:
            singles[data[0]] += 1
            log(f"RX +{now-start:.2f}s gap={gap} HEX={data[0]:02X} DEC={data[0]}")
        else:
            # Avoid collecting unknown multi-byte payloads or credentials.
            log(f"RX +{now-start:.2f}s gap={gap} length={len(data)} (payload omitted)")

    await asyncio.wait_for(client.start_notify(rx, received), 8)
    try:
        log(f"Subscribed. Observing for {seconds} seconds. No application-level writes.")
        deadline = time.monotonic() + seconds
        while client.is_connected and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
    finally:
        log(f"Summary: {sum(sizes.values())} packets; sizes={dict(sizes)}")
        log("Single-byte histogram: " + (", ".join(f"{value:02X}={count}" for value, count in sorted(singles.items())) or "(none)"))
        if client.is_connected:
            try:
                await asyncio.wait_for(client.stop_notify(rx), 5)
            except Exception:
                pass

async def run(args):
    LOG.write_text("", encoding="utf-8")
    for attempt in range(1, 4):
        log(f"Connection attempt {attempt}/3")
        device = await BleakScanner.find_device_by_address(args.address, timeout=5)
        if device is None:
            log("Watch not found.")
            continue
        connected = False
        try:
            async with BleakClient(device, timeout=15) as client:
                connected = True
                log("Connected without requesting pairing.")
                await observe(client, args.seconds)
                return
        except Exception as exc:
            log(f"BLE error: {type(exc).__name__}")
            if connected:
                return
        await asyncio.sleep(2)
    log("No connection established. Keep the watch close and phone Bluetooth off.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--seconds", type=int, default=90, choices=range(10, 181), metavar="10-180")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log("Stopped by user.")
    print("Log:", LOG.resolve())

if __name__ == "__main__":
    main()

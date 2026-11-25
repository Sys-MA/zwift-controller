"""Experimental Gattlink transport diagnostic for an owned Fitbit.

Protocol reference: Google-Health-API/golden-gate, xp/gattlink/gg_gattlink.c
Only answers an observed transport reset request. No device reset, pairing,
authentication, application requests or collection of multi-byte data payloads.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
from pathlib import Path
import time
from bleak import BleakClient, BleakScanner

RX = "abbaff02-e56a-484c-b832-8b17cf6cbfe8"
TX = "abbaff01-e56a-484c-b832-8b17cf6cbfe8"
RESET = b"\x80"
# Version 0, conservative receive and transmit windows of four packets.
COMPLETE = bytes((0x81, 0, 0, 4, 4))
LOG = Path("fitbit-probe.log")

def log(message):
    line = f"[{datetime.now():%H:%M:%S}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as out:
        out.write(line + "\n")

async def observe(client, args):
    rx = client.services.get_characteristic(RX)
    tx = client.services.get_characteristic(TX)
    if rx is None or "notify" not in rx.properties:
        log("Gattlink RX unavailable.")
        return
    if args.handshake and (tx is None or "write-without-response" not in tx.properties):
        log("Gattlink TX unavailable.")
        return
    queue = asyncio.Queue(maxsize=128)
    counts = Counter()
    subscriptions = []
    start = time.monotonic()
    sent = 0
    ready = False

    def receive(sender, data):
        # Only control metadata is retained; do not print session or health payloads.
        name = sender.uuid[:8]
        packet = bytes(data)
        counts[name] += 1
        if not queue.full():
            queue.put_nowait((name, packet, time.monotonic() - start))

    try:
        await asyncio.wait_for(client.start_notify(rx, receive), 8)
        subscriptions.append(rx)
        log("RX subscribed; handshake=" + str(args.handshake))
        if args.other_channels:
            for uuid in (
                "abbafd04-e56a-484c-b832-8b17cf6cbfe8",
                "e90c4308-3984-41f3-be3c-7118093c3300",
                "389e8365-158f-4308-9204-756f29b6a87c",
                "dd1d1206-568f-4b2d-a48c-969d8ad1f457",
            ):
                char = client.services.get_characteristic(uuid)
                if char is not None and ("notify" in char.properties or "indicate" in char.properties):
                    try:
                        await asyncio.wait_for(client.start_notify(char, receive), 5)
                        subscriptions.append(char)
                        log(f"Subscribed {uuid[:8]}")
                    except Exception as exc:
                        log(f"Subscription {uuid[:8]} failed: {type(exc).__name__}")
        deadline = time.monotonic() + args.seconds
        while client.is_connected and time.monotonic() < deadline:
            try:
                name, packet, elapsed = await asyncio.wait_for(queue.get(), min(0.5, max(0.01, deadline-time.monotonic())))
            except asyncio.TimeoutError:
                continue
            if name != RX[:8]:
                log(f"RX {name} +{elapsed:.2f}s length={len(packet)} (omitted)")
                continue
            if packet == RESET:
                log(f"RX +{elapsed:.2f}s 80 (Gattlink reset request)")
                if args.handshake and not ready and sent < 3:
                    await asyncio.wait_for(client.write_gatt_char(tx, COMPLETE, response=False), 5)
                    sent += 1
                    log("TX 81 00 00 04 04 (transport reset complete)")
            elif len(packet) == 5 and packet[0] == 0x81:
                log(f"RX +{elapsed:.2f}s 81: min_version={packet[1]}, max_version={packet[2]}, rx_window={packet[3]}, tx_window={packet[4]}")
                if sent and packet[1] == 0 and packet[2] >= packet[1] and 1 <= packet[3] <= 31 and 1 <= packet[4] <= 31:
                    ready = True
                    log("Transport handshake confirmed. No application authentication attempted.")
            elif packet and not (packet[0] & 0x80):
                log(f"RX +{elapsed:.2f}s Gattlink data candidate: length={len(packet)}, header={packet[0]:02X} (payload omitted)")
                if ready:
                    log("First transport data observed; stopping before upper-layer negotiation.")
                    break
            else:
                log(f"RX +{elapsed:.2f}s unclassified length={len(packet)} (payload omitted)")
        log(f"Connected at end: {client.is_connected}")
    finally:
        log(f"Summary: notifications={dict(counts)} replies={sent} transport_ready={ready}")
        for char in reversed(subscriptions):
            if client.is_connected:
                try:
                    await asyncio.wait_for(client.stop_notify(char), 5)
                except Exception:
                    pass

async def run(args):
    LOG.write_text("", encoding="utf-8")
    for attempt in range(1, 4):
        log(f"Connection attempt {attempt}/3")
        devices = await BleakScanner.discover(timeout=10, return_adv=True)
        selected = next((d for d, adv in devices.values() if d.address.casefold() == args.address.casefold()), None)
        if selected is None:
            log("Watch not detected.")
            continue
        connected = False
        try:
            async with BleakClient(selected, timeout=20) as client:
                connected = True
                log("Connected without requesting pairing. Decline unexpected pairing prompts.")
                await observe(client, args)
                return
        except Exception as exc:
            log(f"BLE error: {type(exc).__name__}")
            if connected:
                return
        await asyncio.sleep(2)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--seconds", type=int, default=30, choices=range(10, 121), metavar="10-120")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--handshake", action="store_true", help="Respond only to a documented Gattlink reset request")
    modes.add_argument("--other-channels", action="store_true", help="Passive metadata observation of other notify/indicate channels")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log("Stopped by user.")
    print("Log:", LOG.resolve())

if __name__ == "__main__":
    main()

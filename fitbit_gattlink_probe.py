"""Experimental Gattlink transport diagnostic for an owned Fitbit.

Protocol reference: Google-Health-API/golden-gate, xp/gattlink/gg_gattlink.c
Answers an observed transport reset request and optionally acknowledges data. No device reset, pairing,
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

def describe_frame(frame):
    """Report only structural metadata, never payload or network addresses."""
    if len(frame) < 4:
        return "Incomplete network header"
    declared = int.from_bytes(frame[2:4], "big")
    if frame[0] & 0x80:
        flags = int.from_bytes(frame[:2], "big")
        proto = {0: "TCP", 0x40: "UDP", 0x80: "ICMP"}.get(flags & 0xc0, "explicit")
        description = f"Compressed IPv4 candidate: flags={flags:04X} length={declared} protocol={proto}"
    elif frame[0] >> 4 == 4 and len(frame) >= 20:
        proto = {6: "TCP", 17: "UDP", 1: "ICMP"}.get(frame[9], "other")
        description = f"IPv4 candidate: length={declared} protocol={proto}"
    else:
        description = "Unknown network framing"
    candidates = []
    for i in range(min(64, max(0, len(frame)-12))):
        if frame[i] in (20, 21, 22, 23) and frame[i+1:i+3] in (b"\xfe\xff", b"\xfe\xfd", b"\xfe\xfc"):
            length = int.from_bytes(frame[i+11:i+13], "big")
            if length <= len(frame)-i-13:
                candidates.append(f"offset={i}, type={frame[i]}, version={frame[i+1:i+3].hex()}, record_length={length}")
    return description + ("; DTLS signature candidate: " + "; ".join(candidates) if candidates else "; no complete DTLS record signature found")

async def observe(client, args):
    rx = client.services.get_characteristic(RX)
    tx = client.services.get_characteristic(TX)
    if rx is None or "notify" not in rx.properties:
        log("Gattlink RX unavailable.")
        return
    if (args.handshake or args.inspect_transport) and (tx is None or "write-without-response" not in tx.properties):
        log("Gattlink TX unavailable.")
        return
    queue = asyncio.Queue(maxsize=128)
    counts = Counter()
    subscriptions = []
    start = time.monotonic()
    sent = 0
    ready = False
    next_psn = 0
    acks = 0
    stream = bytearray()

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
        log("RX subscribed; handshake=" + str(args.handshake or args.inspect_transport))
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
                if (args.handshake or args.inspect_transport) and not ready and sent < 3:
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
                if ready and args.inspect_transport:
                    offset = 1 if packet[0] & 0x40 else 0
                    if len(packet) <= offset + 1:
                        continue
                    if packet[offset] & 0xe0:
                        log("Unexpected data header; stopping.")
                        break
                    psn = packet[offset] & 0x1f
                    if psn == next_psn:
                        stream.extend(packet[offset+1:])
                        next_psn = (next_psn + 1) % 32
                        log(f"PSN={psn} stream_bytes={len(stream)}")
                        if len(stream) >= 4:
                            declared = int.from_bytes(stream[2:4], "big")
                            if 4 <= declared <= len(stream) and (stream[0] & 0x80 or stream[0] >> 4 == 4):
                                log(describe_frame(stream[:declared]))
                                del stream[:declared]
                            else:
                                log(describe_frame(stream))
                    elif not (1 <= (next_psn - psn) % 32 <= 4):
                        log("Out-of-order packet; stopping.")
                        break
                    ack = bytes((0x40 | ((next_psn - 1) % 32),))
                    await asyncio.wait_for(client.write_gatt_char(tx, ack, response=False), 5)
                    acks += 1
                    log(f"TX {ack.hex().upper()} (transport ACK only)")
                    if acks >= 8 or len(stream) > 4096:
                        log("Diagnostic limit reached.")
                        break
                elif ready:
                    log("First transport data observed; stopping before upper-layer negotiation.")
                    break
            else:
                log(f"RX +{elapsed:.2f}s unclassified length={len(packet)} (payload omitted)")
        log(f"Connected at end: {client.is_connected}")
    finally:
        log(f"Summary: notifications={dict(counts)} replies={sent} acks={acks} transport_ready={ready}")
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
    modes.add_argument("--inspect-transport", action="store_true", help="Handshake, inspect network headers, send at most eight transport ACKs")
    modes.add_argument("--other-channels", action="store_true", help="Passive metadata observation of other notify/indicate channels")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log("Stopped by user.")
    print("Log:", LOG.resolve())

if __name__ == "__main__":
    main()

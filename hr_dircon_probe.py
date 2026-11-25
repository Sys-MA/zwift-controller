"""Local WFTNP/DIRCON pairing-screen test; synthetic HR only.

No power, cadence, trainer control, Fitbit access, or external service.
Never use for a ride, race or recorded activity.
Protocol reference: https://github.com/elfrances/wahoo-fitness-tnp
"""
import argparse
import asyncio
import ipaddress
import socket
import struct
import time
from uuid import UUID

TYPE = "_wahoo-fitness-tnp._tcp.local."
NAME = "HR TEST ONLY"
HEADER = struct.Struct("!BBBBH")


def uuid(short):
    return UUID(f"0000{short:04x}-0000-1000-8000-00805f9b34fb").bytes


HR = uuid(0x180D)
MEAS = uuid(0x2A37)
LOCATION = uuid(0x2A38)


def packet(kind, seq, code=0, data=b""):
    return HEADER.pack(1, kind, seq, code, len(data)) + data


def local_ip():
    # UDP connect selects a route without transmitting any data.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]


async def client(reader, writer, ip):
    peer = writer.get_extra_info("peername")
    if not peer or peer[0] not in (ip, "127.0.0.1"):
        writer.close()
        return
    print("TCP connected from local PC", flush=True)
    started = time.monotonic()
    subscribed = False
    notify_seq = 0
    next_notify = time.monotonic()

    async def notify():
        nonlocal notify_seq, next_notify
        bpm = (80, 100, 120, 140)[int((time.monotonic() - started) / 5) % 4]
        writer.write(packet(6, notify_seq, data=MEAS + bytes((0, bpm))))
        await writer.drain()
        notify_seq = (notify_seq + 1) % 256
        next_notify = time.monotonic() + 1
        print("TEST BPM =", bpm, flush=True)

    async def notifications():
        while True:
            if subscribed and time.monotonic() >= next_notify:
                await notify()
            await asyncio.sleep(0.1)

    task = asyncio.create_task(notifications())
    try:
        while True:
            header = await reader.readexactly(HEADER.size)
            version, kind, seq, code, size = HEADER.unpack(header)
            if version != 1 or code != 0 or size > 256:
                print("Unsupported header:", header.hex(), flush=True)
                break
            data = await reader.readexactly(size)
            print(f"RX type={kind} seq={seq} length={size} data={data.hex()}", flush=True)
            status, response = 0, b""
            if kind == 1 and not data:
                response = HR
            elif kind == 2:
                if data == HR:
                    response = HR + MEAS + b"\x04" + LOCATION + b"\x01"
                else:
                    status = 3
            elif kind == 3:
                if data == LOCATION:
                    response = LOCATION + b"\x00"
                else:
                    status = 5 if data == MEAS else 4
            elif kind == 5:
                if len(data) == 17 and data[:16] == MEAS and data[16] in (0, 1):
                    subscribed = bool(data[16])
                    response = MEAS
                    print("HR notifications enabled:", subscribed, flush=True)
                else:
                    status = 5
            elif kind == 4:
                status = 5
            else:
                status = 1
            writer.write(packet(kind, seq, status, response))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        print("TCP disconnected", flush=True)


async def run(ip, seconds):
    from zeroconf import ServiceInfo, Zeroconf

    # Explicit interface only. Requests from other computers are rejected.
    addr = ipaddress.IPv4Address(ip)
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast:
        raise ValueError("Select your LAN IPv4 address.")
    tasks = set()

    def connect(reader, writer):
        if len(tasks) >= 4:
            writer.close()
            return
        task = asyncio.create_task(client(reader, writer, ip))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(connect, ip, 36866)
    zc = None
    info = None
    registered = False
    try:
        zc = Zeroconf(interfaces=[ip])
        info = ServiceInfo(
            TYPE, NAME + "." + TYPE,
            addresses=[addr.packed], port=36866,
            properties={
                "serial-number": "HRTEST0001",
                "mac-address": "02:00:00:00:18:0D",
                "ble-service-uuids": "0x180d",
            },
            server="hr-test-only.local.",
        )
        # Wahoo's service label is 17 bytes, beyond the DNS-SD strict limit.
        await asyncio.to_thread(zc.register_service, info, strict=False)
        registered = True
        print(f"Advertising {NAME} at {ip}:36866 for {seconds}s", flush=True)
        print("TEST ONLY: open MyWhoosh device pairing; do not start a ride.", flush=True)
        print("Waiting for local TCP connection...", flush=True)
        await asyncio.sleep(seconds)
    finally:
        server.close()
        await server.wait_closed()
        if tasks:
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*list(tasks), return_exceptions=True)
        if zc:
            try:
                if registered:
                    await asyncio.to_thread(zc.unregister_service, info)
            finally:
                await asyncio.to_thread(zc.close)
        print("Test stopped.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", help="LAN IPv4 address of this PC")
    parser.add_argument("--seconds", type=int, default=120, choices=range(10, 181), metavar="10-180")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.ip or local_ip(), args.seconds))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Diagnostic failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

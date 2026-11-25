"""Read a paired legacy HRM2 via native Windows RFCOMM, without COM ports."""
import argparse
import re
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="Bluetooth MAC address of the paired HRM2")
    args = parser.parse_args()
    address = args.address.replace("-", ":").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
        parser.error("Expected a Bluetooth MAC address")
    total = 0
    try:
        with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM) as conn:
            conn.settimeout(12)
            print("Connecting directly to RFCOMM channel 1...", flush=True)
            conn.connect((address, 1))
            print("Connected. Listening for up to 20 seconds.", flush=True)
            deadline = time.monotonic() + 20
            conn.settimeout(1)
            while time.monotonic() < deadline and total < 2048:
                try:
                    data = conn.recv(min(128, 2048 - total))
                except socket.timeout:
                    continue
                if not data:
                    print("Remote device closed the connection.")
                    break
                total += len(data)
                print(data.hex(" "), flush=True)
    except (OSError, AttributeError) as exc:
        print(f"RFCOMM diagnostic failed: {type(exc).__name__}: {exc}")
    print("Bytes received:", total)


if __name__ == "__main__":
    main()

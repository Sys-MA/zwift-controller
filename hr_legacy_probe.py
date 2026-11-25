"""Read a paired Sports Tracker HRM2 serial stream, without guessing BPM.
Pair with Windows first, then choose the outgoing Bluetooth COM port.
Requires pyserial only when this standalone diagnostic is used.
"""
import argparse
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Outgoing Bluetooth COM port (e.g. COM5)")
    args = parser.parse_args()
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("Install optional diagnostic dependency: py -3.12 -m pip install pyserial")
        return
    if not args.port:
        for port in list_ports.comports():
            print(port.device, port.description)
        print("Select the outgoing Bluetooth COM port for ST HRM2 using --port.")
        return
    try:
        with serial.Serial(args.port, baudrate=9600, timeout=1) as connection:
            deadline = time.monotonic() + 20
            remaining = 2048
            print("Reading selected port for up to 20 seconds; no BPM decoding.")
            while time.monotonic() < deadline and remaining > 0:
                chunk = connection.read(min(64, remaining))
                if chunk:
                    print(chunk.hex(" "), flush=True)
                    remaining -= len(chunk)
            print("Done. Bytes received:", 2048 - remaining)
    except (OSError, serial.SerialException) as exc:
        print("Serial connection failed:", exc)


if __name__ == "__main__":
    main()

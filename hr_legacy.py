"""Experimental Sports Tracker HRM2 decoder, based on observed frames."""
import re
import socket
import time


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


class HRM2Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        """Yield BPM or None for complete valid HR frames; ignore other frames."""
        self.buffer.extend(data)
        while self.buffer:
            start = self.buffer.find(b"\xfa")
            if start < 0:
                self.buffer.clear()
                break
            del self.buffer[:start]
            if len(self.buffer) < 4:
                break
            if self.buffer[1] ^ self.buffer[2] != 255 or self.buffer[3] != 0x81:
                del self.buffer[0]
                continue
            if len(self.buffer) < 17:
                break
            frame = self.buffer[:17]
            if crc16(frame[:-2]) != int.from_bytes(frame[-2:], "big"):
                del self.buffer[0]
                continue
            del self.buffer[:17]
            bpm = frame[5]
            yield bpm if 25 <= bpm <= 250 else None


def read_hrm2(address, cancel, on_bpm, on_status):
    address = address.strip().replace("-", ":").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
        raise ValueError("Adresse Bluetooth invalide (format XX:XX:XX:XX:XX:XX)")
    decoder = HRM2Decoder()
    with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM) as conn:
        conn.settimeout(12)
        conn.connect((address, 1))
        if cancel.is_set():
            return
        conn.settimeout(1)
        on_status("HRM2 connectée — mesure expérimentale")
        while not cancel.is_set():
            try:
                data = conn.recv(256)
            except socket.timeout:
                continue
            if not data:
                break
            for bpm in decoder.feed(data):
                if not cancel.is_set():
                    on_bpm(bpm, time.monotonic())
        on_status("HRM2 déconnectée")

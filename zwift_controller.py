import asyncio
from datetime import datetime
from bleak import BleakScanner, BleakClient

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

def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}", flush=True)

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

class Controller:
    def __init__(self, device):
        self.device = device
        self.side = "INCONNUE"
        self.state = 0
        self.battery = None

    @property
    def label(self):
        return f"{self.side} [{self.device.address}]"

    def emit(self, state):
        for button, pressed, mask in changes(self.state, state):
            log(f"{self.label} | {button} | {'ENFONCE' if pressed else 'RELACHE'} | 0x{mask:04X}")
        self.state = state

    def notify(self, sender, data):
        state = decode_buttons(data)
        if state is None:
            return
        if self.side == "INCONNUE":
            left, right = bool(state & LEFT_MASK), bool(state & RIGHT_MASK)
            if left != right:
                self.side = "GAUCHE" if left else "DROITE"
                log(f"Manette identifiee : {self.label}")
        self.emit(state)

    def battery_notify(self, sender, data):
        if data and data[0] <= 100 and data[0] != self.battery:
            self.battery = data[0]
            log(f"{self.label} | Batterie : {self.battery}%")

    async def run(self, connection_lock):
        try:
            async with connection_lock:
                log(f"{self.label} | Connexion...")
                client = BleakClient(self.device, timeout=20)
                try:
                    await client.connect()
                    log(f"{self.label} | Connectee")
                    await client.start_notify(ZWIFT_ASYNC, self.notify)
                    await client.start_notify(ZWIFT_SYNC_TX, lambda s, d: None)
                    char = client.services.get_characteristic(ZWIFT_SYNC_RX)
                    if char is None:
                        raise RuntimeError("Sync RX introuvable")
                    response = "write" in char.properties
                    for payload in HANDSHAKE:
                        await client.write_gatt_char(char, payload, response=response)
                        await asyncio.sleep(0.3)
                    log(f"{self.label} | Initialisation terminee")
                    try:
                        battery = client.services.get_characteristic(BATTERY)
                        if battery:
                            if "read" in battery.properties:
                                self.battery_notify(battery, await client.read_gatt_char(battery))
                            if "notify" in battery.properties:
                                await client.start_notify(battery, self.battery_notify)
                    except Exception as exc:
                        log(f"{self.label} | Batterie indisponible : {exc}")
                except BaseException:
                    await client.disconnect()
                    raise
            try:
                while client.is_connected:
                    await asyncio.sleep(3)
                    if client.is_connected:
                        await client.write_gatt_char(char, b"RideOn", response=response)
            finally:
                await client.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"{self.label} | ERREUR : {exc}")
        finally:
            self.emit(0)
            log(f"{self.label} | Deconnectee")

async def main():
    print("MyWhoosh Controller - v0.4")
    print("Reveille les deux manettes. CTRL+C pour quitter.")
    tasks = {}
    lock = asyncio.Lock()
    try:
        while True:
            for address, task in list(tasks.items()):
                if task.done():
                    await task
                    del tasks[address]
            if len(tasks) < 2:
                try:
                    # Do not scan while another connection is being initialized.
                    async with lock:
                        results = await BleakScanner.discover(timeout=5, return_adv=True)
                    for device, adv in results.values():
                        name = adv.local_name or device.name or ""
                        if "zwift click" not in name.casefold():
                            continue
                        address = device.address.upper()
                        if address in tasks or len(tasks) >= 2:
                            continue
                        log(f"Manette detectee : {name} [{address}]")
                        tasks[address] = asyncio.create_task(Controller(device).run(lock))
                except Exception as exc:
                    log(f"Erreur recherche BLE : {exc}")
            await asyncio.sleep(2)
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgramme arrete.")

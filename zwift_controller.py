
import asyncio
from datetime import datetime
from bleak import BleakScanner, BleakClient


CONTROLLERS = {
    "C8:54:6B:F3:6B:A7": "Manette 1",
    "C8:54:6B:08:A9:C2": "Manette 2"
}

ZWIFT_ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_SYNC_RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_SYNC_TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"

HANDSHAKE = [
    bytes.fromhex("52 69 64 65 4F 6E 02 03"),
    bytes.fromhex("00 08 00"),
    bytes.fromhex("00 08 10")
]

# Correspondances provisoires a valider
BUTTONS = {
    0x0001: "GAUCHE",
    0x0002: "HAUT",
    0x0004: "DROITE",
    0x0008: "BAS",
    0x0010: "A",
    0x0020: "B",
    0x0040: "Y",
    0x0080: "Z",
    0x0100: "MOINS",
    0x1000: "PLUS",
    0x2000: "INCONNU_2000"
}


def log(message):
    timestamp = datetime.now().strftime(
        "%H:%M:%S.%f"
    )[:-3]

    print(f"[{timestamp}] {message}", flush=True)


def notification_handler(name):

    previous_state = 0

    def handler(sender, data):

        nonlocal previous_state

        # Ignorer les autres types de messages
        if len(data) < 7:
            return

        if data[0] != 0x23 or data[1] != 0x08:
            return

        # Decoder la valeur Varint
        value = 0

        for index, byte in enumerate(data[2:]):

            value |= (byte & 0x7F) << (index * 7)

            if not byte & 0x80:
                break
        else:
            return

        # Les boutons utilisent une logique inversee
        state = (~value) & 0xFFFFFFFF

        # Ignorer les notifications identiques
        if state == previous_state:
            return

        pressed = state & ~previous_state
        released = previous_state & ~state

        previous_state = state

        for index in range(32):

            mask = 1 << index

            button = BUTTONS.get(
                mask,
                f"INCONNU_{mask:04X}"
            )

            if pressed & mask:

                log(
                    f"{name} | {button} | "
                    f"ENFONCE | 0x{mask:04X}"
                )

            if released & mask:

                log(
                    f"{name} | {button} | "
                    f"RELACHE | 0x{mask:04X}"
                )

    return handler


async def initialize_controller(client, name):

    char = client.services.get_characteristic(
        ZWIFT_SYNC_RX
    )

    if char is None:
        raise RuntimeError("Sync RX introuvable")

    response = "write" in char.properties

    for payload in HANDSHAKE:

        await client.write_gatt_char(
            char,
            payload,
            response=response
        )

        await asyncio.sleep(0.3)

    log(f"{name} | Initialisation terminee")


async def keep_alive(client, name):

    char = client.services.get_characteristic(
        ZWIFT_SYNC_RX
    )

    response = "write" in char.properties

    while client.is_connected:

        await asyncio.sleep(3)

        if not client.is_connected:
            break

        await client.write_gatt_char(
            char,
            b"RideOn",
            response=response
        )


async def connect_controller(device):

    name = CONTROLLERS[device.address.upper()]

    log(f"{name} | Connexion...")

    try:

        async with BleakClient(
            device,
            timeout=20.0
        ) as client:

            log(f"{name} | Connectee")

            await client.start_notify(
                ZWIFT_ASYNC,
                notification_handler(name)
            )

            await client.start_notify(
                ZWIFT_SYNC_TX,
                lambda sender, data: None
            )

            await initialize_controller(
                client,
                name
            )

            await keep_alive(client, name)

    except asyncio.CancelledError:
        raise

    except Exception as e:

        log(f"{name} | ERREUR : {e}")

    finally:

        log(f"{name} | Deconnectee")


async def main():

    print("=" * 60)
    print("MyWhoosh Controller - v0.3")
    print("=" * 60)

    print("\nRecherche des manettes Bluetooth...")
    print("Appuie sur les boutons des deux manettes.\n")

    devices = await BleakScanner.discover(
        timeout=15
    )

    selected = []

    for device in devices:

        address = device.address.upper()

        if address in CONTROLLERS:

            selected.append(device)

            log(
                f"{CONTROLLERS[address]} "
                f"detectee : {address}"
            )

    if not selected:

        log("Aucune manette trouvee")
        return

    log(f"{len(selected)} manette(s) trouvee(s)")

    tasks = [
        asyncio.create_task(
            connect_controller(device)
        )
        for device in selected
    ]

    print("\nAppuie sur CTRL+C pour quitter.\n")

    try:

        await asyncio.gather(*tasks)

    finally:

        for task in tasks:
            task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True
        )


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("\nProgramme arrete.")

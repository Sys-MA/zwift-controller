"""OpenBikeControl v1 TCP controller bridge."""
import asyncio
import socket
import uuid

SERVICE = "_openbikecontrol._tcp.local."
SERVICE_UUID = "d273f680-d548-419d-b9d1-fa0472345229"
BUTTON_IDS = {"PLUS": 1, "MOINS": 2, "HAUT": 0x10, "BAS": 0x11,
              "GAUCHE": 0x12, "DROITE": 0x13, "A": 0x14, "B": 0x15,
              "Y": 0x16, "Z": 0x17}

def button_packet(button, pressed):
    return bytes((1, BUTTON_IDS[button], int(bool(pressed))))

def local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]

class Bridge:
    def __init__(self, log, host=None, port=8765):
        self.log = log
        self.host = host
        self.port = port
        self.server = None
        self.zc = None
        self.info = None
        self.clients = set()
        self.ready = False
        self.battery = 255
        self.heartbeat = None

    async def start(self):
        from zeroconf import ServiceInfo, IPVersion
        from zeroconf.asyncio import AsyncZeroconf
        ip = self.host or local_ip()
        self.server = await asyncio.start_server(self.handle, ip, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        ident = f"{uuid.getnode():012x}"
        name = f"Zwift Controller {ident[-4:]}"
        self.info = ServiceInfo(SERVICE, name + "." + SERVICE,
            addresses=[socket.inet_aton(ip)], port=self.port,
            properties={"version": "1", "id": ident, "name": name,
                "service-uuids": SERVICE_UUID, "manufacturer": "Community",
                "model": "Zwift Click Bridge"},
            server=f"zwift-controller-{ident}.local.")
        self.zc = AsyncZeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
        await self.zc.async_register_service(self.info)
        self.heartbeat = asyncio.create_task(self.periodic())
        self.log(f"OBC | {name} | {ip}:{self.port} | En attente de MyWhoosh")

    def status(self):
        return bytes((2, self.battery, int(self.ready)))

    def broadcast(self, packet):
        for writer in list(self.clients):
            if writer.is_closing() or writer.transport.get_write_buffer_size() > 65536:
                writer.close()
                continue
            try:
                writer.write(packet)
            except (OSError, RuntimeError):
                writer.close()

    def set_ready(self, ready):
        self.ready = ready
        self.broadcast(self.status())

    def set_battery(self, level):
        self.battery = level
        self.broadcast(self.status())

    def button(self, name, pressed):
        if self.ready:
            self.broadcast(button_packet(name, pressed))

    async def periodic(self):
        while True:
            await asyncio.sleep(15)
            self.broadcast(self.status())

    async def handle(self, reader, writer):
        self.clients.add(writer)
        peer = writer.get_extra_info("peername")
        self.log(f"OBC | Client connecte : {peer}")
        try:
            writer.write(self.status())
            await writer.drain()
            while True:
                kind = (await reader.readexactly(1))[0]
                if kind == 3:
                    await reader.readexactly(3)  # Haptics not implemented.
                elif kind == 4:
                    version, size = await reader.readexactly(2)
                    app = (await reader.readexactly(size)).decode("utf-8", "replace")
                    size = (await reader.readexactly(1))[0]
                    app_version = (await reader.readexactly(size)).decode("utf-8", "replace")
                    count = (await reader.readexactly(1))[0]
                    buttons = await reader.readexactly(count)
                    self.log(f"OBC | App {app} {app_version} | v{version} | boutons {buttons.hex(' ')}")
                else:
                    self.log(f"OBC | Message inconnu : {kind:#04x}")
                    break
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()
            self.log(f"OBC | Client deconnecte : {peer}")

    async def close(self):
        if self.heartbeat:
            self.heartbeat.cancel()
            await asyncio.gather(self.heartbeat, return_exceptions=True)
        for writer in list(self.clients):
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.zc:
            try:
                if self.info:
                    await self.zc.async_unregister_service(self.info)
            finally:
                await self.zc.async_close()

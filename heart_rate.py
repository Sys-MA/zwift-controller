"""Optional local-only heart rate display. No relay, storage or background startup."""
import asyncio
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def decode_bpm(data):
    if len(data) < 2:
        return None
    flags = data[0]
    # If contact detection is supported, suppress readings without contact.
    if flags & 4 and not flags & 2:
        return None
    size = 2 if flags & 1 else 1
    if len(data) < 1 + size:
        return None
    value = int.from_bytes(data[1:1 + size], "little")
    return value if 0 < value <= 300 else None


class HeartRateWindow:
    def __init__(self, root):
        self.window = tk.Toplevel(root)
        self.window.title("Cardio (optionnel)")
        self.window.attributes("-topmost", True)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.events = queue.Queue()
        self.worker = None
        self.cancel = threading.Event()
        self.devices = []
        self.closed = False
        self.last_read = None
        self.bpm = tk.StringVar(value="— BPM")
        self.status = tk.StringVar(value="Choisir un capteur Bluetooth BLE")
        self.frame = ttk.Frame(self.window, padding=12)
        self.frame.pack(fill="both", expand=True)
        ttk.Label(self.frame, textvariable=self.bpm,
                  font=("Segoe UI", 28, "bold")).pack()
        ttk.Label(self.frame, textvariable=self.status, wraplength=280).pack(pady=(0, 8))
        self.controls = ttk.Frame(self.frame)
        self.controls.pack(fill="x")
        self.selection = ttk.Combobox(self.controls, state="readonly", width=32)
        self.selection.pack(fill="x")
        buttons = ttk.Frame(self.controls)
        buttons.pack(fill="x", pady=(6, 0))
        self.scan_button = ttk.Button(buttons, text="Rechercher", command=self.scan)
        self.scan_button.pack(side="left")
        self.connect_button = ttk.Button(buttons, text="Connecter", command=self.connect, state="disabled")
        self.connect_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(buttons, text="Arrêter", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.legacy = ttk.Frame(self.controls)
        self.legacy.pack(fill="x", pady=(8, 0))
        ttk.Label(self.legacy, text="Sports Tracker HRM2 (Bluetooth classique)").pack(anchor="w")
        self.address = ttk.Entry(self.legacy, width=24)
        self.address.pack(fill="x", pady=(4, 4))
        self.legacy_button = ttk.Button(self.legacy, text="Connecter HRM2", command=self.connect_legacy)
        self.legacy_button.pack(anchor="w")
        self.compact = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.frame, text="Affichage compact", variable=self.compact,
                        command=self.toggle_compact).pack(anchor="w", pady=(8, 0))
        self.window.after(150, self.poll)

    def toggle_compact(self):
        if self.compact.get():
            self.controls.pack_forget()
        else:
            self.controls.pack(fill="x", before=self.frame.winfo_children()[-1])

    def launch(self, operation):
        if self.closed or self.worker is not None:
            return
        self.cancel.clear()
        self.legacy_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.last_read = None
        self.bpm.set("— BPM")

        def work():
            try:
                asyncio.run(operation())
            except Exception as exc:
                self.events.put(("status", f"Erreur : {exc}"))
            finally:
                self.events.put(("done", None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def scan(self):
        self.devices = []
        self.selection.set("")
        self.selection.configure(values=())
        self.status.set("Recherche Bluetooth (6 s)…")

        async def operation():
            from bleak import BleakScanner
            found = await BleakScanner.discover(timeout=6, return_adv=True)
            devices = [
                (device, adv.local_name or device.name or "Capteur sans nom")
                for device, adv in found.values()
                if SERVICE in [u.lower() for u in adv.service_uuids]
            ]
            if not self.cancel.is_set():
                self.events.put(("devices", devices))

        self.launch(operation)

    def connect(self):
        index = self.selection.current()
        if not 0 <= index < len(self.devices):
            return
        device, name = self.devices[index]
        self.status.set("Connexion : " + name)

        async def operation():
            from bleak import BleakClient
            async with BleakClient(device, timeout=12) as client:
                if self.cancel.is_set():
                    return
                def notification(sender, data):
                    self.events.put(("bpm", (decode_bpm(data), time.monotonic())))
                await asyncio.wait_for(client.start_notify(MEASUREMENT, notification), 10)
                self.events.put(("status", name + " — en attente des BPM"))
                while client.is_connected and not self.cancel.is_set():
                    await asyncio.sleep(0.25)
                if client.is_connected:
                    await asyncio.wait_for(client.stop_notify(MEASUREMENT), 5)
                self.events.put(("status", "Déconnecté"))

        self.launch(operation)

    def connect_legacy(self):
        from hr_legacy import read_hrm2
        address = self.address.get()
        self.status.set("Connexion HRM2…")

        async def operation():
            await asyncio.to_thread(read_hrm2, address, self.cancel,
                lambda bpm, stamp: self.events.put(("bpm", (bpm, stamp))),
                lambda status: self.events.put(("status", status)))

        self.launch(operation)

    def stop(self):
        self.cancel.set()
        self.last_read = None
        self.bpm.set("— BPM")
        self.status.set("Arrêt en cours…")
        self.stop_button.configure(state="disabled")

    def close(self):
        self.closed = True
        self.cancel.set()
        self.window.destroy()

    def poll(self):
        if self.closed:
            return
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status.set(value)
            elif kind == "devices":
                self.devices = value
                self.selection.configure(values=[f"{name} ({dev.address})" for dev, name in value])
                if value:
                    self.selection.current(0)
                self.status.set(f"{len(value)} capteur(s) BLE trouvé(s)" if value else "Aucun capteur BLE standard trouvé")
            elif kind == "bpm" and not self.cancel.is_set():
                bpm, stamp = value
                self.last_read = stamp if bpm is not None else None
                self.bpm.set(f"{bpm} BPM" if bpm is not None else "— BPM")
                self.status.set("Signal reçu" if bpm is not None else "Mesure indisponible")
            elif kind == "done":
                self.worker = None
                self.last_read = None
                self.bpm.set("— BPM")
                if self.cancel.is_set():
                    self.status.set("Arrêté")
                self.legacy_button.configure(state="normal")
                self.scan_button.configure(state="normal")
                self.connect_button.configure(state="normal" if self.devices else "disabled")
                self.stop_button.configure(state="disabled")
        if self.last_read is not None and time.monotonic() - self.last_read > 5:
            self.last_read = None
            self.bpm.set("— BPM")
            self.status.set("Aucune donnée récente")
        self.window.after(150, self.poll)

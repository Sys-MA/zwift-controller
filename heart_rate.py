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
        self.bpm = tk.StringVar(value="—")
        self.status = tk.StringVar(value="Choisir un capteur Bluetooth BLE")
        self.frame = ttk.Frame(self.window, padding=12)
        self.frame.pack(fill="both", expand=True)
        self.card = tk.Frame(self.frame, bg="#192331", padx=12, pady=8)
        self.card.pack(fill="x", pady=(0, 8))
        self.card_top = tk.Frame(self.card, bg="#192331")
        self.card_top.pack(fill="x")
        self.caption = tk.Label(self.card_top, text="CARDIO", bg="#192331",
                                fg="#aeb9c9", font=("Segoe UI", 9, "bold"))
        self.caption.pack(side="left")
        self.close_label = tk.Label(self.card_top, text="×", bg="#192331",
                                    fg="#aeb9c9", cursor="hand2", font=("Segoe UI", 13))
        self.close_label.pack(side="right")
        self.close_label.bind("<Button-1>", lambda event: self.close())
        self.expand_label = tk.Label(self.card_top, text="⚙", bg="#192331",
                                     fg="#aeb9c9", cursor="hand2", font=("Segoe UI", 12))
        self.expand_label.pack(side="right", padx=(0, 8))
        self.expand_label.bind("<Button-1>", self.expand)
        self.reading = tk.Frame(self.card, bg="#192331")
        self.reading.pack(anchor="center", pady=(0, 2))
        self.heart = tk.Canvas(self.reading, width=34, height=34, bg="#192331",
                               highlightthickness=0)
        self.heart.create_polygon(17, 29, 4, 16, 3, 10, 6, 5, 12, 4, 17, 9,
                                  22, 4, 28, 5, 31, 10, 30, 16,
                                  fill="#f87171", outline="", smooth=True)
        self.heart.create_line(6, 17, 12, 17, 15, 11, 19, 22, 22, 17, 28, 17,
                               fill="#192331", width=2, joinstyle="round")
        self.heart.pack(side="left", padx=(0, 7))
        self.digits = tk.Label(self.reading, textvariable=self.bpm, bg="#192331",
                               fg="#ffffff", font=("Segoe UI", 32, "bold"), width=3)
        self.digits.pack(side="left")
        self.unit = tk.Label(self.reading, text="BPM", bg="#192331",
                             fg="#aeb9c9", font=("Segoe UI", 10))
        self.unit.pack(side="left", anchor="s", pady=(0, 10))
        self.card_status = tk.Label(self.card, textvariable=self.status, bg="#192331",
                                    fg="#aeb9c9", wraplength=240, font=("Segoe UI", 9))
        self.card_status.pack()
        for widget in (self.card, self.card_top, self.caption, self.reading,
                       self.heart, self.digits, self.unit, self.card_status):
            widget.bind("<ButtonPress-1>", self.begin_drag)
            widget.bind("<B1-Motion>", self.drag)
        self.window.bind("<Escape>", self.expand)
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
        self.compact_button = ttk.Checkbutton(self.frame, text="Affichage compact", variable=self.compact,
                        command=self.toggle_compact)
        self.compact_button.pack(anchor="w", pady=(8, 0))
        self.expand_label.pack_forget()
        self.window.after(150, self.poll)

    def begin_drag(self, event):
        self.drag_offset = (event.x_root - self.window.winfo_x(),
                            event.y_root - self.window.winfo_y())

    def drag(self, event):
        if not self.compact.get():
            return
        x = event.x_root - self.drag_offset[0]
        y = event.y_root - self.drag_offset[1]
        self.window.geometry(f"{x:+d}{y:+d}")

    def expand(self, event=None):
        if self.compact.get():
            self.compact.set(False)
            self.toggle_compact()

    def toggle_compact(self):
        if self.compact.get():
            self.controls.pack_forget()
            self.compact_button.pack_forget()
            self.frame.configure(padding=0)
            self.card.pack_configure(pady=0)
            self.expand_label.pack(side="right", padx=(0, 8))
            self.window.overrideredirect(True)
            self.window.attributes("-alpha", 0.90)
        else:
            self.window.overrideredirect(False)
            self.window.attributes("-alpha", 1.0)
            self.frame.configure(padding=12)
            self.card.pack_configure(pady=(0, 8))
            self.expand_label.pack_forget()
            self.controls.pack(fill="x")
            self.compact_button.pack(anchor="w", pady=(8, 0))
        self.window.attributes("-topmost", True)
        self.window.update_idletasks()
        self.window.geometry("")

    def launch(self, operation):
        if self.closed or self.worker is not None:
            return
        self.cancel.clear()
        self.legacy_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.last_read = None
        self.bpm.set("—")

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
        self.bpm.set("—")
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
                self.bpm.set(str(bpm) if bpm is not None else "—")
                self.status.set("Signal reçu" if bpm is not None else "Mesure indisponible")
            elif kind == "done":
                self.worker = None
                self.last_read = None
                self.bpm.set("—")
                if self.cancel.is_set():
                    self.status.set("Arrêté")
                self.legacy_button.configure(state="normal")
                self.scan_button.configure(state="normal")
                self.connect_button.configure(state="normal" if self.devices else "disabled")
                self.stop_button.configure(state="disabled")
        if self.last_read is not None and time.monotonic() - self.last_read > 5:
            self.last_read = None
            self.bpm.set("—")
            self.status.set("Aucune donnée récente")
        self.window.after(150, self.poll)

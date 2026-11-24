"""Windows desktop UI for Zwift Controller."""
import asyncio
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import zwift_controller as core
from keyboard_output import DEFAULT_KEYS, key_code

if getattr(sys, "frozen", False):
    core.CONFIG = Path(os.environ["LOCALAPPDATA"]) / "ZwiftController" / "config.json"
    core.CONFIG.parent.mkdir(parents=True, exist_ok=True)

def startup_command():
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--minimized"])
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return subprocess.list2cmdline([str(pythonw), str(Path(__file__).resolve()), "--minimized"])

def set_autostart(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Run",
                        0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "ZwiftController", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, "ZwiftController")
            except FileNotFoundError:
                pass

def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            return bool(winreg.QueryValueEx(key, "ZwiftController")[0])
    except FileNotFoundError:
        return False

class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.worker = None
        self.loop = None
        self.task = None
        self.tray = None
        self.quitting = False
        self.restart = False
        self.config = core.load_config()
        root.title("Zwift Controller 0.6")
        root.geometry("650x690")
        root.minsize(590, 640)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame = ttk.Frame(root, padding=20)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="Zwift Controller", font=("Segoe UI", 20, "bold")).grid(sticky="w")
        self.status = tk.StringVar(value="En attente")
        ttk.Label(frame, textvariable=self.status).grid(sticky="w", pady=(0,12))
        batteries = ttk.Frame(frame)
        batteries.grid(sticky="ew")
        self.levels = {}
        for i, side in enumerate(("GAUCHE", "DROITE")):
            batteries.columnconfigure(i, weight=1)
            panel = ttk.LabelFrame(batteries, text=side.title(), padding=12)
            panel.grid(row=0, column=i, sticky="ew", padx=4)
            value = tk.StringVar(value="Batterie : inconnue")
            self.levels[side] = value
            ttk.Label(panel, textvariable=value).pack(anchor="w")
        options = ttk.Frame(frame)
        options.grid(sticky="ew", pady=12)
        self.mode = tk.StringVar(value=self.config.get("output_mode", "Clavier"))
        ttk.Label(options, text="Mode :").pack(side="left")
        ttk.Combobox(options, textvariable=self.mode, state="readonly",
                     values=("Clavier", "OpenBikeControl", "Bluetooth seulement"), width=23).pack(side="left", padx=8)
        self.auto = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(options, text="Demarrer avec Windows", variable=self.auto,
                        command=self.toggle_auto).pack(side="right")
        mappings = ttk.LabelFrame(frame, text="Touches MyWhoosh (modifiables)", padding=10)
        mappings.grid(sticky="ew")
        mappings.columnconfigure(1, weight=1)
        mappings.columnconfigure(3, weight=1)
        self.keys = {}
        current = DEFAULT_KEYS | self.config.get("keyboard_mapping", {})
        for i, button in enumerate(DEFAULT_KEYS):
            row, col = divmod(i, 2)
            col *= 2
            ttk.Label(mappings, text=button).grid(row=row, column=col, sticky="w", padx=5, pady=3)
            var = tk.StringVar(value=current.get(button) or "")
            self.keys[button] = var
            ttk.Entry(mappings, textvariable=var, width=12).grid(row=row, column=col+1, sticky="ew", padx=6)
        ttk.Label(frame, text="I/K valides. Autres touches a tester. Vide = desactive.").grid(sticky="w", pady=5)
        controls = ttk.Frame(frame)
        controls.grid(sticky="ew", pady=6)
        ttk.Button(controls, text="Appliquer / reconnecter", command=self.apply).pack(side="left")
        ttk.Button(controls, text="Arreter", command=self.stop).pack(side="left", padx=8)
        ttk.Button(controls, text="Quitter", command=self.quit).pack(side="right")
        self.logs = tk.Text(frame, height=8, wrap="word", state="disabled", font=("Consolas", 9))
        self.logs.grid(sticky="nsew", pady=(8,0))
        self.setup_tray()
        self.root.after(100, self.poll)

    def toggle_auto(self):
        try:
            set_autostart(self.auto.get())
        except OSError as exc:
            self.auto.set(autostart_enabled())
            messagebox.showerror("Demarrage automatique", str(exc))

    def setup_tray(self):
        import pystray
        from PIL import Image, ImageDraw
        image = Image.new("RGBA", (64, 64), "#153047")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 15, 56, 49), radius=12, fill="#2e9e80")
        draw.line((17,32,33,32), fill="white", width=4)
        draw.line((25,24,25,40), fill="white", width=4)
        draw.ellipse((40,27,49,36), fill="white")
        self.tray = pystray.Icon("ZwiftController", image, "Zwift Controller",
            menu=pystray.Menu(
                pystray.MenuItem("Ouvrir", lambda *a: self.events.put(("show", None)), default=True),
                pystray.MenuItem("Quitter", lambda *a: self.events.put(("quit", None)))))
        threading.Thread(target=self.tray.run, daemon=True).start()

    def hide(self):
        if self.tray is not None and self.tray.visible:
            self.root.withdraw()
        else:
            self.root.iconify()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.status.set("Recherche Bluetooth...")
        mode = self.mode.get()
        args = {"Clavier": ["--keyboard"], "OpenBikeControl": ["--obc"],
                "Bluetooth seulement": ["--no-obc"]}[mode]
        def run():
            async def entry():
                self.loop = asyncio.get_running_loop()
                self.task = asyncio.current_task()
                try:
                    await core.main(args)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self.events.put(("log", f"ERREUR : {exc!r}"))
                finally:
                    self.task = None
                    self.loop = None
            try:
                asyncio.run(entry())
            finally:
                self.events.put(("stopped", None))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def stop(self):
        self.restart = False
        self.status.set("Arret en cours...")
        loop, task = self.loop, self.task
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        elif not self.worker or not self.worker.is_alive():
            self.status.set("Arrete")
        else:
            self.root.after(100, self.cancel_pending)

    def cancel_pending(self):
        loop, task = self.loop, self.task
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        elif self.worker and self.worker.is_alive():
            self.root.after(100, self.cancel_pending)

    def apply(self):
        mapping = {b: v.get().strip().upper() or None for b,v in self.keys.items()}
        try:
            for key in mapping.values():
                key_code(key)
            self.config.update(keyboard_mapping=mapping, output_mode=self.mode.get())
            core.CONFIG.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Configuration", str(exc))
            return
        if self.worker and self.worker.is_alive():
            self.stop()
            self.restart = True
        else:
            self.start()

    def quit(self):
        self.quitting = True
        self.stop()
        self.root.after(100, self.finish_quit)

    def finish_quit(self):
        if self.worker and self.worker.is_alive():
            self.root.after(100, self.finish_quit)
            return
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def poll(self):
        for _ in range(150):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "show":
                self.root.deiconify()
                self.root.lift()
            elif event == "quit":
                self.quit()
            elif event == "stopped":
                self.status.set("Arrete")
                if self.restart and not self.quitting:
                    self.restart = False
                    self.root.after(150, self.start)
            elif event == "log":
                match = re.search(r"(GAUCHE|DROITE) \| Batterie : (\d+)%", value)
                if match:
                    self.levels[match[1]].set(f"Batterie : {match[2]} % (derniere lecture)")
                if "GAUCHE | Initialisation terminee" in value:
                    self.status.set("Connecte - manettes pretes")
                elif "GAUCHE | Deconnectee" in value:
                    self.status.set("Deconnecte - reconnexion")
                self.logs.configure(state="normal")
                self.logs.insert("end", value + "\n")
                if int(self.logs.index("end-1c").split(".")[0]) > 500:
                    self.logs.delete("1.0", "101.0")
                self.logs.see("end")
                self.logs.configure(state="disabled")
        if not self.quitting:
            self.root.after(100, self.poll)

def main():
    root = tk.Tk()
    app = App(root)
    original = core.log
    def log(message):
        original(message)
        app.events.put(("log", message))
    core.log = log
    app.start()
    if "--minimized" in sys.argv:
        root.after(700, app.hide)
    root.mainloop()

if __name__ == "__main__":
    main()

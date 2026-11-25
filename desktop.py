"""Windows desktop UI for Zwift Controller."""
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import shutil
import webbrowser
from instance_lock import single_instance, InstanceUnavailable
from version import VERSION
import updater
from bike_icon import make_icon

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
        self.reset_event = threading.Event()
        self.reset_count = 0
        self.config = core.load_config()
        self.log_history = deque(maxlen=500)
        self.update_release = None
        self.update_busy = False
        self.battery_values = {}
        self.low_battery_warned = set()
        self.logs = None
        self.dialogs = {}
        self.button_labels = {}
        root.title("Zwift Controller " + VERSION)
        from PIL import ImageTk
        self.window_icon = ImageTk.PhotoImage(make_icon(64))
        root.iconphoto(True, self.window_icon)
        root.geometry("560x520")
        root.minsize(530, 510)
        root.configure(bg="#f1f5f9")
        root.protocol("WM_DELETE_WINDOW", self.hide)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Dashboard.TFrame", background="#f1f5f9")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Dashboard.TLabel", background="#f1f5f9", foreground="#172c40")
        style.configure("Muted.TLabel", background="#f1f5f9", foreground="#526478")
        style.configure("AuthWarning.TLabel", background="#f1f5f9", foreground="#b91c1c", font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabel", background="#ffffff", foreground="#172c40")
        style.configure("Battery.TLabel", background="#ffffff", foreground="#172c40", font=("Segoe UI", 28, "bold"))
        style.configure("Primary.TButton", background="#153047", foreground="#ffffff", padding=(12, 10), borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#254b66"), ("pressed", "#102437")])
        style.configure("TButton", padding=(10, 7))

        frame = ttk.Frame(root, padding=20, style="Dashboard.TFrame")
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame, style="Dashboard.TFrame")
        header.pack(fill="x")
        ttk.Label(header, image=self.window_icon, style="Dashboard.TLabel").pack(side="left", padx=(0,12))
        titles = ttk.Frame(header, style="Dashboard.TFrame")
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="Zwift Controller", style="Dashboard.TLabel", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        self.status = tk.StringVar(value="En attente")
        ttk.Label(titles, textvariable=self.status, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(header, text=VERSION, style="Muted.TLabel").pack(side="right", anchor="n")

        batteries = ttk.Frame(frame, style="Dashboard.TFrame")
        batteries.pack(fill="x", pady=(18, 12))
        self.levels = {}
        self.connections = {}
        self.connection_labels = {}
        self.battery_bars = {}
        self.recovery_times = {}
        self.button_counts = {}
        self.auth_states = {}
        self.connected = set()
        for i, side in enumerate(("GAUCHE", "DROITE")):
            batteries.columnconfigure(i, weight=1, uniform="cards")
            panel = ttk.Frame(batteries, padding=14, style="Card.TFrame")
            panel.grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i == 0 else (6, 0))
            ttk.Label(panel, text="Manette " + side.lower(), style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w")
            self.connections[side] = tk.StringVar(value="En attente")
            label = ttk.Label(panel, textvariable=self.connections[side], style="Card.TLabel")
            label.pack(anchor="w", pady=(3, 5))
            self.connection_labels[side] = label
            self.levels[side] = tk.StringVar(value="-- %")
            ttk.Label(panel, textvariable=self.levels[side], style="Battery.TLabel").pack(anchor="w")
            bar = ttk.Progressbar(panel, maximum=100, mode="determinate")
            bar.pack(fill="x", pady=(0, 8))
            self.battery_bars[side] = bar
            self.recovery_times[side] = tk.StringVar(value="Reconnexion : -")
            self.button_counts[side] = tk.IntVar(value=0)
            self.auth_states[side] = tk.StringVar(value="")
            ttk.Label(panel, textvariable=self.recovery_times[side], style="Card.TLabel", font=("Segoe UI", 9)).pack(anchor="w")
            chips = ttk.Frame(panel, style="Card.TFrame")
            chips.pack(fill="x", pady=(9, 0))
            buttons = (("HAUT","↑"),("GAUCHE","←"),("DROITE","→"),("BAS","↓"),("MOINS","−")) if i == 0 else (("A","A"),("B","B"),("Y","Y"),("Z","Z"),("PLUS","+"))
            for button, symbol in buttons:
                chip = tk.Label(chips, text=symbol, width=2, bg="#e8eef4", fg="#153047", font=("Segoe UI", 10, "bold"), padx=2, pady=4)
                chip.pack(side="left", padx=(0,3))
                self.button_labels[button] = chip

        self.auth = tk.StringVar(value="")
        self.game_status = tk.StringVar(value="MyWhoosh : premier plan non verifie")
        self.last_button = tk.StringVar(value="Dernier bouton : -")
        self.auth_warning = ttk.Label(frame, textvariable=self.auth, style="AuthWarning.TLabel", wraplength=460)
        self.game_label = ttk.Label(frame, textvariable=self.game_status, style="Muted.TLabel", wraplength=460)
        self.game_label.pack(anchor="w", pady=(3, 0))
        ttk.Label(frame, textvariable=self.last_button, style="Dashboard.TLabel").pack(anchor="w", pady=(3, 12))

        ttk.Button(frame, text="Configurer les boutons", style="Primary.TButton", command=self.open_buttons).pack(fill="x")
        controls = ttk.Frame(frame, style="Dashboard.TFrame")
        controls.pack(fill="x", pady=(8,0))
        ttk.Button(controls, text="Reconnecter", command=self.reconnect).pack(side="left")
        ttk.Button(controls, text="Options", command=self.open_options).pack(side="left", padx=6)
        ttk.Button(controls, text="Diagnostics", command=self.open_diagnostics).pack(side="right")
        footer = ttk.Frame(frame, style="Dashboard.TFrame")
        footer.pack(fill="x", side="bottom", pady=(8,0))
        ttk.Button(footer, text="Arreter", command=self.stop).pack(side="left")
        ttk.Button(footer, text="Quitter", command=self.quit).pack(side="right")
        self.update_button = ttk.Button(frame, text="Mise a jour disponible", command=self.install_update)
        self.reset_label = tk.StringVar(value="Reboots : 0")
        self.setup_tray()
        self.root.after(100, self.poll)
        self.root.after(1500, self.check_update)

    def notify(self, title, message):
        if self.tray:
            try:
                self.tray.notify(message, title)
            except Exception:
                pass

    def refresh_tray(self):
        if self.tray:
            levels = [("G" if side == "GAUCHE" else "D") + ": " + (str(self.battery_values[side]) + "%" if side in self.connected and side in self.battery_values else "--") for side in self.connections]
            self.tray.title = "Zwift Controller | " + " | ".join(levels)

    def check_update(self, manual=False):
        if self.update_busy or self.quitting:
            return
        self.update_busy = True
        def run():
            try:
                self.events.put(("update_checked", (updater.check(), manual)))
            except Exception as exc:
                self.events.put(("update_error", (str(exc), manual)))
        threading.Thread(target=run, daemon=True).start()

    def install_update(self):
        if self.update_busy or not self.update_release:
            return
        release = self.update_release
        if not getattr(sys, "frozen", False) or not release["installable"]:
            webbrowser.open(updater.RELEASES)
            return
        if not messagebox.askyesno("Mise a jour", f"Installer {release['version']}? Les manettes seront deconnectees pendant le redemarrage."):
            return
        self.update_busy = True
        self.update_button.configure(state="disabled", text="Telechargement...")
        def run():
            try:
                self.events.put(("update_staged", updater.stage(release)))
            except Exception as exc:
                self.events.put(("update_error", (str(exc), True)))
        threading.Thread(target=run, daemon=True).start()

    def dialog(self, name, title):
        existing = self.dialogs.get(name)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return None
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        self.dialogs[name] = win
        return win

    def open_buttons(self):
        win = self.dialog("buttons", "Configurer les boutons")
        if win is None:
            return
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Raccourcis MyWhoosh", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0,12))
        ttk.Label(frame, text="Bouton").grid(row=1, column=0, sticky="w")
        ttk.Label(frame, text="Touche").grid(row=1, column=1, sticky="w")
        ttk.Label(frame, text="Mode de pression").grid(row=1, column=2, sticky="w")
        ttk.Label(frame, text="Action").grid(row=1, column=3, sticky="w")
        keys, modes = {}, {}
        current = DEFAULT_KEYS | self.config.get("keyboard_mapping", {})
        descriptions = {"I":"Rapport plus facile", "K":"Rapport plus difficile", "A":"Direction gauche", "D":"Direction droite", "U":"Afficher / masquer infos", "C":"Camera", "1":"Interaction 1", "2":"Interaction 2", "3":"Interaction 3"}
        order = ("GAUCHE","DROITE","HAUT","BAS","MOINS","A","B","Y","Z","PLUS")
        for row, button in enumerate(order, 2):
            ttk.Label(frame, text=button).grid(row=row, column=0, sticky="w", pady=4)
            key = tk.StringVar(value=current.get(button) or "")
            keys[button] = key
            ttk.Entry(frame, textvariable=key, width=9).grid(row=row, column=1, padx=8, sticky="ew")
            mode = tk.StringVar(value=self.config.get("keyboard_modes", {}).get(button, "Simple"))
            modes[button] = mode
            ttk.Combobox(frame, textvariable=mode, values=("Simple","Maintenue","Repetition"), state="readonly", width=13).grid(row=row, column=2, padx=8)
            action = tk.StringVar()
            def refresh(*args, k=key, v=action):
                value = k.get().strip().upper()
                v.set(descriptions.get(value, "Personnalisee" if value else "Desactive"))
            key.trace_add("write", refresh)
            refresh()
            ttk.Label(frame, textvariable=action, width=24).grid(row=row, column=3, sticky="w")
        ttk.Label(frame, text="A/D et C a valider en roulant. Touche vide = desactive.").grid(row=12, column=0, columnspan=4, sticky="w", pady=10)
        def defaults():
            for button in keys:
                keys[button].set(DEFAULT_KEYS[button])
                modes[button].set("Simple")
        def save():
            mapping = {button: var.get().strip().upper() or None for button, var in keys.items()}
            try:
                for key in mapping.values():
                    key_code(key)
            except ValueError as exc:
                messagebox.showerror("Raccourcis", str(exc), parent=win)
                return
            if self.save_settings({"keyboard_mapping": mapping, "keyboard_mapping_version": 2, "keyboard_modes": {b:v.get() for b,v in modes.items()}}, win):
                win.destroy()
        actions = ttk.Frame(frame)
        actions.grid(row=13, column=0, columnspan=4, sticky="ew")
        ttk.Button(actions, text="Valeurs par defaut", command=defaults).pack(side="left")
        ttk.Button(actions, text="Enregistrer", style="Primary.TButton", command=save).pack(side="right")
        ttk.Button(actions, text="Annuler", command=win.destroy).pack(side="right", padx=6)

    def open_options(self):
        win = self.dialog("options", "Options")
        if win is None:
            return
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)
        mode = tk.StringVar(value=self.config.get("output_mode", "Clavier"))
        auto_reset = tk.BooleanVar(value=self.config.get("auto_reset", False))
        fast = tk.BooleanVar(value=self.config.get("fast_reconnect", True))
        diag = tk.BooleanVar(value=self.config.get("diagnostics", False))
        auto = tk.BooleanVar(value=autostart_enabled())
        ttk.Label(frame, text="Mode de sortie", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Combobox(frame, textvariable=mode, state="readonly", values=("Clavier","OpenBikeControl","Bluetooth seulement"), width=30).pack(fill="x", pady=(4,12))
        for title, var in (("Demarrer avec Windows", auto), ("Reconnexion rapide (cache GATT Windows)", fast), ("Redemarrage de secours (gauche)", auto_reset), ("Logs de diagnostic detailles", diag)):
            ttk.Checkbutton(frame, text=title, variable=var).pack(anchor="w", pady=4)
        def save():
            try:
                set_autostart(auto.get())
            except OSError as exc:
                messagebox.showerror("Options", str(exc), parent=win)
                return
            if self.save_settings({"output_mode":mode.get(),"auto_reset":auto_reset.get(),"fast_reconnect":fast.get(),"diagnostics":diag.get()}, win):
                win.destroy()
        ttk.Button(frame, text="Verifier les mises a jour", command=lambda: self.check_update(True)).pack(anchor="w", pady=(14,0))
        ttk.Button(frame, text="Enregistrer", style="Primary.TButton", command=save).pack(anchor="e", pady=(14,0))

    def open_diagnostics(self):
        win = self.dialog("diagnostics", "Diagnostics")
        if win is None:
            return
        win.geometry("680x430")
        frame = ttk.Frame(win, padding=15)
        frame.pack(fill="both", expand=True)
        for side in ("GAUCHE","DROITE"):
            row = ttk.Frame(frame)
            row.pack(fill="x")
            ttk.Label(row, text=side + " : ", font=("Segoe UI", 10, "bold")).pack(side="left")
            ttk.Label(row, textvariable=self.auth_states[side], foreground="#b91c1c").pack(side="left")
            ttk.Label(row, textvariable=self.button_counts[side]).pack(side="right")
            ttk.Label(row, text="Pressions :").pack(side="right")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Exporter le log", command=self.export_log).pack(side="left")
        ttk.Button(actions, text="Reset gauche", command=self.request_reset).pack(side="left", padx=6)
        ttk.Label(actions, textvariable=self.reset_label).pack(side="left")
        self.logs = tk.Text(frame, wrap="word", font=("Consolas", 9), height=12)
        scrollbar = ttk.Scrollbar(frame, command=self.logs.yview)
        self.logs.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.logs.pack(fill="both", expand=True)
        self.logs.insert("end", "\n".join(self.log_history) + "\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")
        def close():
            self.logs = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close)

    def save_settings(self, updates, parent):
        candidate = dict(self.config)
        candidate.update(updates)
        try:
            core.CONFIG.write_text(json.dumps(candidate, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Configuration", str(exc), parent=parent)
            return False
        self.config = candidate
        self.reconnect()
        return True

    def reconnect(self):
        if self.worker and self.worker.is_alive():
            self.stop()
            self.restart = True
        else:
            self.start()

    def export_log(self):
        source = core.CONFIG.with_name("debug.log")
        if not source.exists():
            messagebox.showinfo("Diagnostic", "Activer les logs et appliquer les reglages.")
            return
        destination = filedialog.asksaveasfilename(defaultextension=".log", initialfile="zwift-debug.log")
        if destination:
            try:
                shutil.copyfile(source, destination)
            except OSError as exc:
                messagebox.showerror("Diagnostic", str(exc))

    def setup_tray(self):
        import pystray
        image = make_icon(64)
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

    def request_reset(self):
        if self.worker and self.worker.is_alive():
            self.reset_event.set()
        else:
            self.start()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.status.set("Recherche Bluetooth...")
        self.connected.clear()
        self.battery_values.clear()
        self.refresh_tray()
        self.reset_event.clear()
        self.auth.set("")
        self.auth_warning.pack_forget()
        for label in self.button_labels.values():
            label.configure(bg="#e8eef4", fg="#153047")
        for side in self.connections:
            self.connections[side].set("En attente")
            self.connection_labels[side].configure(foreground="#526478")
            self.levels[side].set("-- %")
            self.battery_bars[side]["value"] = 0
            self.auth_states[side].set("")
            self.button_counts[side].set(0)
        mode = self.config.get("output_mode", "Clavier")
        args = {"Clavier": ["--keyboard"], "OpenBikeControl": ["--obc"],
                "Bluetooth seulement": ["--no-obc"]}[mode]
        args += ["--dual"]
        if not self.config.get("auto_reset", False):
            args.append("--no-auto-reset")
        if self.config.get("fast_reconnect", True):
            args.append("--fast-reconnect")
        if self.config.get("diagnostics", False):
            args.append("--debug")
        def run():
            async def entry():
                self.loop = asyncio.get_running_loop()
                self.task = asyncio.current_task()
                try:
                    await core.main(args, reset_event=self.reset_event)
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
        self.reset_event.clear()
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
                self.connected.clear()
                self.refresh_tray()
                for side in self.connections:
                    self.connections[side].set("Arretee")
                    self.connection_labels[side].configure(foreground="#526478")
                if self.restart and not self.quitting:
                    self.restart = False
                    self.root.after(150, self.start)
            elif event == "update_checked":
                release, manual = value
                self.update_busy = False
                self.update_release = release
                if release:
                    self.update_button.configure(text=f"Mise a jour disponible : {release['version']}", state="normal")
                    if not self.update_button.winfo_manager():
                        self.update_button.pack(fill="x", pady=(8,0), before=self.game_label)
                else:
                    self.update_button.pack_forget()
                    if manual:
                        messagebox.showinfo("Mises a jour", "Aucune nouvelle version stable accessible.")
            elif event == "update_error":
                error, manual = value
                self.update_busy = False
                if self.update_release:
                    self.update_button.configure(text=f"Mise a jour disponible : {self.update_release['version']}", state="normal")
                if manual:
                    messagebox.showerror("Mises a jour", error)
                else:
                    self.log_history.append("UPDATE | " + error)
            elif event == "update_staged":
                self.update_busy = False
                staged, expected = value
                try:
                    updater.launch_installer(staged, expected)
                except Exception as exc:
                    staged.unlink(missing_ok=True)
                    self.update_button.configure(state="normal", text="Reessayer la mise a jour")
                    messagebox.showerror("Mise a jour", str(exc))
                else:
                    self.quit()
            elif event == "log":
                if value.startswith("GAUCHE | RESET |"):
                    self.reset_count += 1
                    self.reset_label.set(f"Reboots : {self.reset_count}")
                match = re.search(r"(GAUCHE|DROITE) \| Batterie : (\d+)%", value)
                if match:
                    self.levels[match[1]].set(f"{match[2]} %")
                    level = int(match[2])
                    side = match[1]
                    self.battery_bars[side]["value"] = level
                    self.battery_values[side] = level
                    self.refresh_tray()
                    if level < 15 and side not in self.low_battery_warned:
                        self.low_battery_warned.add(side)
                        self.notify("Batterie faible", f"Manette {side.lower()} : {level}%")
                    elif level >= 15:
                        self.low_battery_warned.discard(side)
                button = re.match(r"(GAUCHE|DROITE) \| (\w+) \| (ENFONCE|RELACHE) \|", value)
                if button and button[2] in self.button_labels:
                    pressed = button[3] == "ENFONCE"
                    label = self.button_labels[button[2]]
                    label.configure(bg="#16805d" if pressed else "#e8eef4", fg="white" if pressed else "#153047")
                    if pressed:
                        self.button_counts[button[1]].set(self.button_counts[button[1]].get()+1)
                        key = self.config.get("keyboard_mapping", {}).get(button[2], DEFAULT_KEYS.get(button[2]))
                        self.last_button.set(f"Dernier bouton : {button[2]} -> {key or '-'}")
                timing = re.match(r"(GAUCHE|DROITE) \| RECOVERY \| initialisation en ([0-9.]+)s", value)
                if timing:
                    self.recovery_times[timing[1]].set(f"Reconnexion : {timing[2]} s")
                if value.startswith("CLAVIER | FOCUS |"):
                    self.game_status.set(value.split(" | ")[-1])
                if value.startswith("CLAVIER | SendInput incomplet"):
                    self.game_status.set("Injection clavier impossible : verifier les droits administrateur")
                if " | AUTH | " in value:
                    side = value.split(" | ")[0]
                    if side in self.auth_states:
                        self.auth_states[side].set("Authentification Zwift requise")
                    self.auth.set("Authentification Zwift requise. Arreter le programme, puis jumeler dans Zwift.")
                    if not self.auth_warning.winfo_manager():
                        self.auth_warning.pack(before=self.game_label, anchor="w", fill="x", pady=(0, 5))
                    if self.tray:
                        try:
                            self.tray.notify("Jumeler les manettes dans Zwift avant de reprendre.", "Authentification Zwift")
                        except Exception:
                            pass
                for side in self.connections:
                    if value.startswith(f"{side} | Initialisation terminee"):
                        self.connected.add(side)
                        self.refresh_tray()
                        self.connections[side].set("Connectee")
                        self.connection_labels[side].configure(foreground="#16805d")
                    elif value.startswith(f"{side} | Deconnectee"):
                        self.connected.discard(side)
                        self.refresh_tray()
                        self.connections[side].set("Reconnexion...")
                        self.connection_labels[side].configure(foreground="#a15c12")
                    elif value.startswith(f"{side} | HEALTH | ") and side in self.connected:
                        health = value.rsplit(" | ",1)[-1]
                        self.connections[side].set({"ok":"Connectee", "silent":"Connectee, sans donnees", "suspect":"Communication a verifier"}.get(health,"Connectee"))
                        self.connection_labels[side].configure(foreground="#16805d" if health == "ok" else "#a15c12")
                    elif value.startswith(f"{side} | Connexion..."):
                        self.connections[side].set("Connexion...")
                        self.connection_labels[side].configure(foreground="#526478")
                self.status.set(f"{len(self.connected)}/2 manettes connectees")
                self.log_history.append(value)
                if self.logs is not None and self.logs.winfo_exists():
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
    try:
        with single_instance():
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
    except InstanceUnavailable as exc:
        root.withdraw()
        messagebox.showinfo("Zwift Controller", str(exc))
        root.destroy()

if __name__ == "__main__":
    main()

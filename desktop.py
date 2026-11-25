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
from game_presence import GamePolicy, game_running
from zwift_auth import Session, AuthError
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

def install_button_styles(root, style):
    """Rounded nine-slice ttk backgrounds; keep native button focus and commands."""
    from PIL import Image, ImageDraw, ImageTk

    images = []
    def background(fill, outline):
        scale = 3
        # ttk/clam can composite transparent image corners over its own grey
        # background instead of the parent frame. Use the actual surface color.
        image = Image.new("RGB", (32 * scale, 32 * scale), "#f1f5f9")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1 * scale, 1 * scale, 31 * scale - 1, 31 * scale - 1),
                               radius=9 * scale, fill=fill, outline=outline, width=scale)
        photo = ImageTk.PhotoImage(image.resize((32, 32), Image.Resampling.LANCZOS), master=root)
        images.append(photo)
        return photo

    palettes = {
        "TButton": ("#ffffff", "#cbd5e1", "#eaf1f7", "#dce7f0", "#153047", "#64748b"),
        "Primary.TButton": ("#153047", "#153047", "#254b66", "#102437", "#ffffff", "#64748b"),
        "Update.TButton": ("#16805d", "#16805d", "#117253", "#0d5e44", "#ffffff", "#64748b"),
        "Danger.TButton": ("#fff7f7", "#e9c6c6", "#fce8e8", "#f8dada", "#a32929", "#64748b"),
    }
    for index, (name, colors) in enumerate(palettes.items()):
        fill, border, hover, pressed, text, muted = colors
        normal = background(fill, border)
        active = background(hover, border)
        down = background(pressed, border)
        focus = background(fill, "#3b82f6")
        active_focus = background(hover, "#3b82f6")
        disabled = background("#e8edf2", "#dce3eb")
        element = f"Rounded{index}.background"
        style.element_create(element, "image", normal,
                             ("disabled", disabled), ("pressed", down),
                             ("active", "focus", active_focus),
                             ("focus", focus), ("active", active),
                             border=10, padding=0, sticky="nsew")
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"})]})]})])
        style.configure(name, background="#f1f5f9", foreground=text, font=("Segoe UI", 10),
                        padding=(12, 9) if name == "Primary.TButton" else (10, 7),
                        anchor="center")
        style.map(name, background=[("disabled", "#f1f5f9"), ("!disabled", "#f1f5f9")],
                  foreground=[("disabled", muted), ("!disabled", text)])
    # Tk does not keep Python references to images used by style elements.
    root._rounded_button_images = images


class ModernSelect(tk.Frame):
    """Flat readonly selector with a padded, keyboard-accessible popup."""
    def __init__(self, parent, *, textvariable, values, width=30):
        super().__init__(parent, bg="#ffffff", highlightthickness=1,
                         highlightbackground="#cbd5e1", highlightcolor="#3b82f6",
                         takefocus=True, cursor="hand2")
        self.variable = textvariable
        self.values = tuple(values)
        self.popup = None
        self.rows = []
        self.index = 0
        self.text = tk.Label(self, textvariable=textvariable, bg="#ffffff",
                             fg="#153047", font=("Segoe UI", 10), anchor="w",
                             width=width, padx=11, pady=8, cursor="hand2")
        self.text.pack(side="left", fill="x", expand=True)
        arrow = tk.Canvas(self, width=30, height=24, bg="#ffffff",
                          highlightthickness=0, bd=0, cursor="hand2")
        arrow.create_line(9, 9, 15, 15, 21, 9, fill="#526478", width=2,
                          capstyle="round", joinstyle="round")
        arrow.pack(side="right", padx=(0, 3))
        for widget in (self, self.text, arrow):
            widget.bind("<Button-1>", self.toggle)
        for key in ("<Return>", "<space>", "<Down>", "<Alt-Down>"):
            self.bind(key, self.toggle)
        self.bind("<Up>", self.toggle)
        self.bind("<Destroy>", self.on_destroy, add="+")

    def toggle(self, event=None):
        if self.popup is not None:
            self.close()
            return "break"
        self.focus_set()
        self.open()
        return "break"

    def open(self):
        owner = self.winfo_toplevel()
        popup = tk.Toplevel(owner)
        self.popup = popup
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(owner)
        popup.configure(bg="#cbd5e1", padx=1, pady=1)
        content = tk.Frame(popup, bg="#ffffff", padx=4, pady=4)
        content.pack(fill="both", expand=True)
        self.rows = []
        self.index = self.values.index(self.variable.get()) if self.variable.get() in self.values else 0
        for index, value in enumerate(self.values):
            row = tk.Label(content, text=value, bg="#ffffff", fg="#153047",
                           font=("Segoe UI", 10), anchor="w", padx=10, pady=8,
                           cursor="hand2")
            row.pack(fill="x")
            row.bind("<Enter>", lambda event, i=index: self.highlight(i))
            row.bind("<ButtonRelease-1>", lambda event, i=index: self.choose(i))
            self.rows.append(row)
        self.highlight(self.index)
        popup.update_idletasks()
        width = max(self.winfo_width(), popup.winfo_reqwidth())
        height = popup.winfo_reqheight()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 3
        if y + height > self.winfo_vrooty() + self.winfo_vrootheight():
            y = self.winfo_rooty() - height - 3
        popup.geometry(f"{width}x{height}{x:+d}{y:+d}")
        popup.bind("<Up>", lambda event: self.move(-1))
        popup.bind("<Down>", lambda event: self.move(1))
        popup.bind("<Home>", lambda event: self.highlight(0))
        popup.bind("<End>", lambda event: self.highlight(len(self.values)-1))
        popup.bind("<Return>", lambda event: self.choose(self.index))
        popup.bind("<space>", lambda event: self.choose(self.index))
        popup.bind("<Escape>", lambda event: self.close())
        popup.bind("<Tab>", lambda event: self.close())
        popup.bind("<ButtonPress-1>", self.outside_click)
        popup.bind("<FocusOut>", lambda event: popup.after_idle(self.check_focus))
        popup.deiconify()
        popup.lift()
        popup.grab_set()
        popup.focus_set()

    def highlight(self, index):
        self.index = index
        for i, row in enumerate(self.rows):
            row.configure(bg="#e0eaf4" if i == index else "#ffffff")
        return "break"

    def move(self, direction):
        return self.highlight((self.index + direction) % len(self.values))

    def choose(self, index):
        self.variable.set(self.values[index])
        self.close()
        self.event_generate("<<ComboboxSelected>>")
        return "break"

    def outside_click(self, event):
        popup = self.popup
        if popup is not None and not (
                popup.winfo_rootx() <= event.x_root < popup.winfo_rootx() + popup.winfo_width()
                and popup.winfo_rooty() <= event.y_root < popup.winfo_rooty() + popup.winfo_height()):
            self.close()
            return "break"

    def check_focus(self):
        if self.popup is not None:
            try:
                focus = self.popup.focus_get()
                if focus is None or focus.winfo_toplevel() != self.popup:
                    self.close(restore=False)
            except tk.TclError:
                self.close(restore=False)

    def close(self, restore=True):
        popup, self.popup = self.popup, None
        if popup is not None:
            try:
                popup.grab_release()
                popup.destroy()
            except tk.TclError:
                pass
        self.rows = []
        if restore and self.winfo_exists():
            self.focus_set()
        return "break"

    def on_destroy(self, event):
        if event.widget == self:
            self.close(restore=False)


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
        self.stopping = False
        self.game_policy = GamePolicy()
        self.reset_event = threading.Event()
        self.auth_reconnect = threading.Event()
        self.zwift_session = Session()
        self.auth_busy = False
        self.account_status = tk.StringVar(value=self.account_status_text())
        self.account_controls = []
        self.account_status_label = None
        self.account_status.trace_add("write", self.refresh_account_status_color)
        self.reset_count = 0
        self.config = core.load_config()
        self.log_history = deque(maxlen=500)
        self.update_release = None
        self.update_busy = False
        self.update_downloading = False
        self.battery_values = {}
        self.low_battery_warned = set()
        self.logs = None
        self.dialogs = {}
        self.button_labels = {}
        root.title("Zwift Controller " + VERSION)
        from PIL import ImageTk
        self.window_icon = ImageTk.PhotoImage(make_icon(64))
        root.iconphoto(True, self.window_icon)
        root.geometry("520x370")
        root.minsize(500, 350)
        root.configure(bg="#f1f5f9")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#f1f5f9")
        style.configure("TFrame", background="#f1f5f9")
        style.configure("TLabel", background="#f1f5f9")
        style.configure("Dashboard.TFrame", background="#f1f5f9")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Dashboard.TLabel", background="#f1f5f9", foreground="#172c40")
        style.configure("Muted.TLabel", background="#f1f5f9", foreground="#526478")
        style.configure("AuthWarning.TLabel", background="#f1f5f9", foreground="#b91c1c", font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabel", background="#ffffff", foreground="#172c40")
        style.configure("Battery.TLabel", background="#ffffff", foreground="#172c40", font=("Segoe UI", 13, "bold"))
        style.configure("CardMuted.TLabel", background="#ffffff", foreground="#526478", font=("Segoe UI", 9))
        for name, color in (("Battery", "#16805d"), ("BatteryLow", "#b91c1c"), ("BatteryMedium", "#b7791f"), ("BatteryUnknown", "#94a3b8")):
            style.configure(name + ".Horizontal.TProgressbar", background=color, troughcolor="#e8eef4",
                            bordercolor="#e8eef4", lightcolor=color, darkcolor=color, thickness=7, borderwidth=0)
        install_button_styles(root, style)

        style.configure("TEntry", padding=(9, 6), fieldbackground="#ffffff",
                        foreground="#153047", bordercolor="#cbd5e1",
                        lightcolor="#ffffff", darkcolor="#ffffff", borderwidth=1)
        style.map("TEntry", bordercolor=[("focus", "#3b82f6")],
                  fieldbackground=[("disabled", "#e8edf2"), ("!disabled", "#ffffff")])

        frame = ttk.Frame(root, padding=20, style="Dashboard.TFrame")
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame, style="Dashboard.TFrame")
        header.pack(fill="x")
        ttk.Label(header, image=self.window_icon, style="Dashboard.TLabel").pack(side="left", padx=(0,12))
        titles = ttk.Frame(header, style="Dashboard.TFrame")
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="Zwift Controller", style="Dashboard.TLabel", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        self.status = tk.StringVar(value="En attente")
        ttk.Label(titles, textvariable=self.status, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(header, text=VERSION, style="Muted.TLabel").pack(side="right", anchor="n")

        batteries = ttk.Frame(frame, style="Dashboard.TFrame")
        batteries.pack(fill="x", pady=(16, 12))
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
            panel = ttk.Frame(batteries, padding=12, style="Card.TFrame")
            panel.grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i == 0 else (6, 0))
            ttk.Label(panel, text="Manette " + side.lower(), style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w")
            self.connections[side] = tk.StringVar(value="En attente")
            label = ttk.Label(panel, textvariable=self.connections[side], style="Card.TLabel")
            label.pack(anchor="w", pady=(3, 10))
            self.connection_labels[side] = label
            battery_row = ttk.Frame(panel, style="Card.TFrame")
            battery_row.pack(fill="x")
            ttk.Label(battery_row, text="Batterie", style="CardMuted.TLabel").pack(side="left")
            self.levels[side] = tk.StringVar(value="—")
            ttk.Label(battery_row, textvariable=self.levels[side], style="Battery.TLabel").pack(side="right")
            bar = ttk.Progressbar(panel, maximum=100, mode="determinate", style="BatteryUnknown.Horizontal.TProgressbar")
            bar.pack(fill="x", pady=(4, 6))
            self.battery_bars[side] = bar
            self.recovery_times[side] = tk.StringVar(value="Reconnexion : -")
            self.button_counts[side] = tk.IntVar(value=0)
            self.auth_states[side] = tk.StringVar(value="")
            ttk.Label(panel, text="Activité des boutons", style="CardMuted.TLabel").pack(anchor="w", pady=(7, 4))
            chips = ttk.Frame(panel, style="Card.TFrame")
            chips.pack(fill="x")
            buttons = (("HAUT","↑"),("GAUCHE","←"),("DROITE","→"),("BAS","↓"),("MOINS","−")) if i == 0 else (("A","A"),("B","B"),("Y","Y"),("Z","Z"),("PLUS","+"))
            for button, symbol in buttons:
                chip = tk.Label(chips, text=symbol, width=2, bg="#e8eef4", fg="#153047", font=("Segoe UI", 10, "bold"), padx=2, pady=4)
                chips.columnconfigure(len(chips.grid_slaves()), weight=1, uniform="chips")
                chip.grid(row=0, column=len(chips.grid_slaves()), sticky="ew", padx=2)
                self.button_labels[button] = chip

        self.auth = tk.StringVar(value="")
        self.keyboard_warning = tk.StringVar(value="")
        self.auth_warning = ttk.Label(frame, textvariable=self.auth, style="AuthWarning.TLabel", wraplength=460)
        self.keyboard_warning_label = ttk.Label(frame, textvariable=self.keyboard_warning, style="AuthWarning.TLabel", wraplength=460)
        self.configure_button = ttk.Button(frame, text="Configurer les boutons", style="Primary.TButton", command=self.open_buttons)
        self.configure_button.pack(fill="x", pady=(6,0))
        controls = ttk.Frame(frame, style="Dashboard.TFrame")
        controls.pack(fill="x", pady=(8,0))
        for index, (label, command) in enumerate((("Reconnecter", self.reconnect), ("Options", self.open_options), ("Diagnostics", self.open_diagnostics))):
            controls.columnconfigure(index, weight=1, uniform="actions")
            ttk.Button(controls, text=label, command=command).grid(row=0, column=index, sticky="ew", padx=(0, 5) if index < 2 else (0, 0))
        footer = ttk.Frame(frame, style="Dashboard.TFrame")
        footer.pack(fill="x", pady=(10,0))
        ttk.Button(footer, text="Arrêter", style="Danger.TButton", command=self.stop).pack(side="left")
        ttk.Button(footer, text="Quitter", command=self.quit).pack(side="right")
        self.update_button = ttk.Button(frame, text="Mise a jour disponible", style="Update.TButton", command=self.install_update)
        self.reset_label = tk.StringVar(value="Reboots : 0")
        self.setup_tray()
        self.root.after(100, self.poll)
        self.root.after(1500, self.periodic_update_check)
        # Fit the requested widget height even on higher-DPI displays.
        root.update_idletasks()
        root.minsize(max(500, root.winfo_reqwidth()), max(350, root.winfo_reqheight()))

    def notify(self, title, message):
        if self.tray:
            try:
                self.tray.notify(message, title)
            except Exception:
                pass

    def clear_battery(self, side):
        self.levels[side].set("—")
        self.battery_bars[side].configure(value=0, style="BatteryUnknown.Horizontal.TProgressbar")
        self.battery_values.pop(side, None)

    def refresh_tray(self):
        if self.tray:
            levels = [("G" if side == "GAUCHE" else "D") + ": " + (str(self.battery_values[side]) + "%" if side in self.connected and side in self.battery_values else "--") for side in self.connections]
            self.tray.title = "Zwift Controller | " + " | ".join(levels)

    def periodic_update_check(self):
        if not self.quitting:
            self.check_update()
            self.root.after(6 * 60 * 60 * 1000, self.periodic_update_check)

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
        self.update_downloading = True
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
        win.configure(bg="#f1f5f9")
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
            ModernSelect(frame, textvariable=mode, values=("Simple","Maintenue","Repetition"), width=12).grid(row=row, column=2, padx=8)
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
        auto_game = tk.BooleanVar(value=self.config.get("auto_mywhoosh", False))
        close_quits = tk.BooleanVar(value=self.config.get("close_quits", False))
        auto = tk.BooleanVar(value=autostart_enabled())
        ttk.Label(frame, text="Mode de sortie", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ModernSelect(frame, textvariable=mode, values=("Clavier","OpenBikeControl","Bluetooth seulement"), width=30).pack(fill="x", pady=(4,12))
        for title, var in (("Démarrer avec Windows", auto), ("Quitter complètement en fermant la fenêtre (X)", close_quits), ("Connecter les manettes seulement lorsque MyWhoosh est ouvert", auto_game), ("Reconnexion rapide (cache GATT Windows)", fast), ("Redemarrage de secours (gauche)", auto_reset), ("Logs de diagnostic detailles", diag)):
            ttk.Checkbutton(frame, text=title, variable=var).pack(anchor="w", pady=4)
        ttk.Label(frame, text="En mode automatique, les manettes sont libérées après 10 secondes sans MyWhoosh. Les commandes manuelles restent disponibles.", wraplength=390, foreground="#526478").pack(anchor="w", pady=(6,0))
        def save():
            try:
                set_autostart(auto.get())
            except OSError as exc:
                messagebox.showerror("Options", str(exc), parent=win)
                return
            if self.save_settings({"output_mode":mode.get(),"auto_reset":auto_reset.get(),"fast_reconnect":fast.get(),"diagnostics":diag.get(),"auto_mywhoosh":auto_game.get(),"close_quits":close_quits.get()}, win):
                win.destroy()
        ttk.Button(frame, text="Compte Zwift", command=self.open_account).pack(anchor="w", pady=(14,0))
        ttk.Button(frame, text="Vérifier les mises à jour", command=lambda: self.check_update(True)).pack(anchor="w", pady=(8,0))
        ttk.Button(frame, text="Enregistrer", style="Primary.TButton", command=save).pack(anchor="e", pady=(14,0))

    def account_status_text(self):
        if self.zwift_session.ready:
            if self.zwift_session.storage_warning:
                return "Compte connecté — session non enregistrée (chiffrement indisponible)"
            return "Compte Zwift connecté — session sauvegardée" if self.zwift_session.persist else "Compte Zwift connecté — pour cette utilisation seulement"
        if self.zwift_session.storage_warning:
            return "Session enregistrée illisible. Reconnectez le compte."
        return "Aucun compte Zwift connecté"

    def refresh_account_status_color(self, *_):
        label = self.account_status_label
        if label is not None and label.winfo_exists():
            color = ("#526478" if self.auth_busy else
                     "#a15c12" if self.zwift_session.storage_warning else
                     "#16805d" if self.zwift_session.ready else "#526478")
            label.configure(foreground=color)

    def open_account(self):
        win = self.dialog("account", "Compte Zwift")
        if win is None:
            return
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Authentification automatique", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self.account_status_label = ttk.Label(frame, textvariable=self.account_status, wraplength=390,
                                              font=("Segoe UI", 11, "bold"))
        self.account_status_label.pack(anchor="w", pady=(6,10))
        self.refresh_account_status_color()
        ttk.Label(frame, text="Connexion facultative. Utilise un service Zwift non documente. Le mot de passe est transmis directement au service Zwift et n'est pas conserve.", wraplength=390, justify="left").pack(anchor="w", pady=(0,12))
        email = tk.StringVar()
        password = tk.StringVar()
        remember = tk.BooleanVar(value=True)
        ttk.Label(frame, text="Courriel").pack(anchor="w")
        mail_entry = ttk.Entry(frame, textvariable=email, width=43)
        mail_entry.pack(fill="x", pady=(3,8))
        ttk.Label(frame, text="Mot de passe").pack(anchor="w")
        password_entry = ttk.Entry(frame, textvariable=password, show="*")
        password_entry.pack(fill="x", pady=(3,8))
        remember_control = ttk.Checkbutton(frame, text="Conserver la session chiffree dans Windows", variable=remember)
        remember_control.pack(anchor="w")
        def login():
            username, secret = email.get().strip(), password.get()
            if not username or not secret:
                messagebox.showinfo("Compte Zwift", "Entrer le courriel et le mot de passe.", parent=win)
                return
            password.set("")
            persist = remember.get()
            self.account_action("login", lambda: self.zwift_session.login(username, secret, persist))
        def logout():
            if messagebox.askyesno("Compte Zwift", "Supprimer la session locale?", parent=win):
                self.account_action("logout", self.zwift_session.logout)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(14,0))
        signin = ttk.Button(actions, text="Se connecter", style="Primary.TButton", command=login)
        signin.pack(side="left")
        signout = ttk.Button(actions, text="Déconnecter", style="Danger.TButton", command=logout)
        signout.pack(side="right")
        self.account_controls = [mail_entry, password_entry, remember_control, signin, signout]
        def close():
            password.set("")
            self.account_controls = []
            self.account_status_label = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close)
        self.refresh_account_controls()

    def refresh_account_controls(self):
        for widget in self.account_controls:
            if widget.winfo_exists():
                widget.configure(state="disabled" if self.auth_busy else "normal")

    def account_action(self, action, operation):
        if self.auth_busy:
            return
        self.auth_busy = True
        self.account_status.set("Communication avec Zwift..." if action == "login" else "Suppression de la session...")
        self.refresh_account_controls()
        def run():
            try:
                operation()
            except AuthError as exc:
                self.events.put(("account_result", (action, str(exc))))
            except Exception:
                self.events.put(("account_result", (action, "Operation impossible. Verifier la session Windows.")))
            else:
                self.events.put(("account_result", (action, None)))
        threading.Thread(target=run, daemon=True).start()

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
            ttk.Label(row, textvariable=self.recovery_times[side], foreground="#526478").pack(side="left", padx=(0,8))
            ttk.Label(row, textvariable=self.auth_states[side], foreground="#b91c1c").pack(side="left")
            ttk.Label(row, textvariable=self.button_counts[side]).pack(side="right")
            ttk.Label(row, text="Pressions :").pack(side="right")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Exporter le log", command=self.export_log).pack(side="left")
        ttk.Button(actions, text="Redémarrer la gauche", style="Danger.TButton", command=self.request_reset).pack(side="left", padx=6)
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
        was_auto = self.auto_game_enabled()
        self.config = candidate
        if self.auto_game_enabled():
            if not was_auto:
                self.game_policy = GamePolicy()
                self.sample_game()
            if self.game_policy.desired:
                self.restart_worker()
            else:
                self.apply_game_policy()
        else:
            self.restart_worker()
        return True

    def auto_game_enabled(self):
        return bool(self.config.get("auto_mywhoosh", False))

    def idle_status(self):
        if self.auto_game_enabled():
            if self.game_policy.override is False:
                return "Arrêté — prochaine ouverture de MyWhoosh"
            if self.game_policy.running is None:
                return "Détection de MyWhoosh..."
            if not self.game_policy.desired:
                return "En attente de MyWhoosh"
        return "Arrêté"

    def sample_game(self):
        try:
            running = game_running()
        except (OSError, AttributeError):
            running = None
        self.game_policy.observe(running)

    def apply_game_policy(self):
        if self.quitting or not self.auto_game_enabled():
            return
        if self.game_policy.desired:
            if self.stopping:
                self.restart = True
            elif self.worker is None:
                self.start()
        else:
            self.restart = False
            if self.worker is not None:
                self.stop_worker()
            else:
                self.status.set(self.idle_status())

    def game_tick(self):
        if self.quitting:
            return
        if self.auto_game_enabled():
            self.sample_game()
            self.apply_game_policy()
        self.root.after(2000, self.game_tick)

    def reconnect(self):
        if self.auto_game_enabled():
            self.game_policy.manual(True)
        self.restart_worker()

    def restart_worker(self):
        if self.worker is not None:
            self.stop_worker()
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

    def on_close(self):
        if self.config.get("close_quits", False):
            self.quit()
        else:
            self.hide()

    def hide(self):
        if self.tray is not None and self.tray.visible:
            self.root.withdraw()
        else:
            self.root.iconify()

    def request_reset(self):
        if self.worker is not None and not self.stopping:
            self.reset_event.set()
        else:
            self.reconnect()

    def start(self):
        if self.quitting or self.worker is not None:
            return
        self.stopping = False
        self.status.set("Recherche Bluetooth...")
        self.connected.clear()
        self.battery_values.clear()
        self.refresh_tray()
        self.reset_event.clear()
        self.auth_reconnect.clear()
        self.auth.set("")
        self.auth_warning.pack_forget()
        self.keyboard_warning.set("")
        self.keyboard_warning_label.pack_forget()
        for label in self.button_labels.values():
            label.configure(bg="#e8eef4", fg="#153047")
        for side in self.connections:
            self.connections[side].set("En attente")
            self.connection_labels[side].configure(foreground="#526478")
            self.clear_battery(side)
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
                    await core.main(args, reset_event=self.reset_event, auth_client=self.zwift_session, auth_reconnect=self.auth_reconnect)
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
        if self.auto_game_enabled():
            self.game_policy.manual(False)
        self.stop_worker()

    def stop_worker(self):
        self.restart = False
        if self.stopping:
            return
        if self.worker is None:
            self.status.set(self.idle_status())
            return
        self.stopping = True
        self.reset_event.clear()
        self.status.set("Arrêt en cours...")
        loop, task = self.loop, self.task
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        elif self.worker.is_alive():
            self.root.after(100, self.cancel_pending)

    def cancel_pending(self):
        if not self.stopping:
            return
        loop, task = self.loop, self.task
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        elif self.worker and self.worker.is_alive():
            self.root.after(100, self.cancel_pending)

    def quit(self):
        if self.quitting:
            return
        if self.update_downloading:
            messagebox.showinfo("Mise a jour", "Le telechargement est en cours. Attendre la fin avant de quitter.")
            return
        self.quitting = True
        self.stop_worker()
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
                self.worker = None
                self.stopping = False
                self.status.set(self.idle_status())
                self.connected.clear()
                for side in self.connections:
                    self.clear_battery(side)
                    self.connections[side].set("En veille" if self.auto_game_enabled() and not self.game_policy.desired else "Arrêtée")
                    self.connection_labels[side].configure(foreground="#526478")
                self.refresh_tray()
                for label in self.button_labels.values():
                    label.configure(bg="#e8eef4", fg="#153047")
                if self.restart and not self.quitting:
                    self.restart = False
                    if not self.auto_game_enabled() or self.game_policy.desired:
                        self.start()
            elif event == "account_result":
                action, error = value
                self.auth_busy = False
                self.account_status.set(self.account_status_text())
                self.refresh_account_controls()
                if error:
                    messagebox.showerror("Compte Zwift", error)
                else:
                    if self.worker and self.worker.is_alive():
                        self.auth_reconnect.set()
                    if action == "login":
                        self.log_history.append("AUTH | Session Zwift ouverte; reconnexion gauche demandee")
                    else:
                        self.log_history.append("AUTH | Session locale supprimee")
            elif event == "update_checked":
                release, manual = value
                self.update_busy = False
                previous = self.update_release
                self.update_release = release
                if release:
                    if not previous or previous["version"] != release["version"]:
                        self.log_history.append(f"UPDATE | Version {release['version']} disponible")
                        self.notify("Mise a jour disponible", f"Zwift Controller {release['version']}")
                    self.update_button.configure(text=f"Mise a jour disponible : {release['version']}", state="normal")
                    if not self.update_button.winfo_manager():
                        self.update_button.pack(fill="x", pady=(8,0), before=self.configure_button)
                else:
                    self.update_button.pack_forget()
                    if manual:
                        messagebox.showinfo("Mises a jour", "Aucune nouvelle version stable accessible.")
            elif event == "update_error":
                error, manual = value
                self.update_busy = False
                self.update_downloading = False
                self.log_history.append("UPDATE | " + error)
                if self.update_release:
                    self.update_button.configure(text=f"Mise a jour disponible : {self.update_release['version']}", state="normal")
                if manual:
                    messagebox.showerror("Mises a jour", error)
            elif event == "update_staged":
                self.update_busy = False
                self.update_downloading = False
                staged, expected = value
                try:
                    updater.launch_installer(staged, expected)
                    self.log_history.append("UPDATE | Installation lancee apres fermeture")
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
                    bar_style = "BatteryLow" if level < 15 else "BatteryMedium" if level < 30 else "Battery"
                    self.battery_bars[side].configure(value=level, style=bar_style + ".Horizontal.TProgressbar")
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
                timing = re.match(r"(GAUCHE|DROITE) \| RECOVERY \| initialisation en ([0-9.]+)s", value)
                if timing:
                    self.recovery_times[timing[1]].set(f"Reconnexion : {timing[2]} s")
                if value.startswith("CLAVIER | SendInput incomplet"):
                    self.keyboard_warning.set("Injection clavier impossible : verifier les droits administrateur")
                    if not self.keyboard_warning_label.winfo_manager():
                        self.keyboard_warning_label.pack(before=self.configure_button, anchor="w", fill="x", pady=(0,5))
                if " | AUTH_OK | " in value:
                    side = value.split(" | ")[0]
                    if side in self.auth_states:
                        self.auth_states[side].set("")
                    if not any(var.get() for var in self.auth_states.values()):
                        self.auth.set("")
                        self.auth_warning.pack_forget()
                    if not self.auth_busy:
                        self.account_status.set(self.account_status_text())
                elif " | AUTH | " in value:
                    side = value.split(" | ")[0]
                    if side in self.auth_states:
                        first = not self.auth_states[side].get()
                        self.auth_states[side].set("Authentification a verifier")
                        message = ("Authentification Zwift : reponse non confirmee. Consulter les diagnostics."
                                   if self.zwift_session.ready else
                                   "Authentification Zwift requise. Options > Compte Zwift ou jumelage dans Zwift.")
                        self.auth.set(message)
                        if not self.auth_warning.winfo_manager():
                            self.auth_warning.pack(before=self.configure_button, anchor="w", fill="x", pady=(0,5))
                        if first:
                            self.notify("Authentification Zwift", message)
                    if not self.auth_busy:
                        self.account_status.set(self.account_status_text())
                for side in self.connections:
                    if value.startswith(f"{side} | Initialisation terminee"):
                        self.connected.add(side)
                        self.refresh_tray()
                        self.connections[side].set("Connectée")
                        self.connection_labels[side].configure(foreground="#16805d")
                    elif value.startswith(f"{side} | Deconnectee"):
                        self.connected.discard(side)
                        self.clear_battery(side)
                        self.refresh_tray()
                        self.connections[side].set("Reconnexion...")
                        self.connection_labels[side].configure(foreground="#a15c12")
                    elif value.startswith(f"{side} | HEALTH | ") and side in self.connected:
                        health = value.rsplit(" | ",1)[-1]
                        self.connections[side].set({"ok":"Connectée", "silent":"Connectée, sans données", "suspect":"Communication à vérifier"}.get(health,"Connectée"))
                        self.connection_labels[side].configure(foreground="#16805d" if health == "ok" else "#a15c12")
                    elif value.startswith(f"{side} | Connexion..."):
                        self.connections[side].set("Connexion...")
                        self.connection_labels[side].configure(foreground="#526478")
                if not self.stopping and self.worker is not None:
                    self.status.set(f"{len(self.connected)}/2 manettes connectées")
                self.log_history.append(value)
                if self.logs is not None and self.logs.winfo_exists():
                    self.logs.configure(state="normal")
                    self.logs.insert("end", value + "\n")
                    if int(self.logs.index("end-1c").split(".")[0]) > 500:
                        self.logs.delete("1.0", "101.0")
                    self.logs.see("end")
                    self.logs.configure(state="disabled")
        if not self.quitting:
            # Accommodate alerts or the update action without clipping controls.
            self.root.update_idletasks()
            needed = max(350, self.root.winfo_reqheight())
            if self.root.minsize()[1] != needed:
                self.root.minsize(max(500, self.root.winfo_reqwidth()), needed)
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
            if not app.auto_game_enabled():
                app.start()
            app.game_tick()
            if "--minimized" in sys.argv:
                root.after(700, app.hide)
            root.mainloop()
    except InstanceUnavailable as exc:
        root.withdraw()
        messagebox.showinfo("Zwift Controller", str(exc))
        root.destroy()

if __name__ == "__main__":
    main()

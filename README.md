# Zwift Controller v0.6

Windows desktop bridge for Zwift Click V2 and MyWhoosh. Keyboard mode is the default; I/K gear shortcuts were confirmed on hardware in v0.5.1. OpenBikeControl is experimental.

## Run

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m unittest discover -s tests -v
py -3.12 desktop.py
```

Keep MyWhoosh in the foreground while riding. Close the desktop window to minimize to the tray; use Quit to disconnect. Edit the ten key mappings and click Apply to reconnect. Empty mappings disable a button. Only I/K game actions are confirmed. The optional Windows autostart uses the current user's Run registry key and starts minimized.

## Console modes

`py -3.12 zwift_controller.py` defaults to keyboard output; `--keyboard` remains supported. Use `--obc` for experimental network output or `--no-obc` for Bluetooth only. Use `--discover` to find controller addresses and edit config.json. The right controller connects temporarily to read battery, normally every 900 seconds. `--no-right-battery` disables these reads.

## Build executable

Run `powershell -ExecutionPolicy Bypass -File .\build.ps1` from the repository. The script installs build dependencies, runs tests, and builds dist/ZwiftController.exe. The executable stores config.json in %LOCALAPPDATA%/ZwiftController; source mode uses the repository config.json. Move the executable to a permanent location before enabling autostart. GUI and packaged executable require Windows validation.

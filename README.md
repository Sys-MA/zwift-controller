# Zwift Controller v0.7.0

Windows desktop bridge for Zwift Click V2 and MyWhoosh. Keyboard mode defaults to I/K for gear changes; other mappings remain provisional. OpenBikeControl is experimental.

## Run

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m unittest discover -s tests -v
py -3.12 desktop.py
```

The desktop uses two independent Bluetooth connections by default. Each controller has its own status and battery reading. Windows cached GATT reconnection is enabled by default, with an uncached discovery fallback. Preventive left-controller reboot is disabled by default. Apply saves settings and reconnects. Reset gauche requests a left-only manual reset. Closing the window minimizes to the tray; Quit disconnects. Keep MyWhoosh in the foreground and run both programs at the same Windows privilege level.

## Zwift authentication

If the left controller sends repeated FF 03 messages, the desktop recommends renewing authentication in the official Zwift application. Stop the bridge, pair both controllers in Zwift, close Zwift and restart the bridge. A short hardware test confirmed that both controllers continued working after official pairing; authorization lifetime remains to be measured. No automatic Zwift account authentication is implemented. The optional emergency reboot may interrupt input and is not a substitute for authentication.

## Console modes

```powershell
py -3.12 zwift_controller.py --dual --no-auto-reset --fast-reconnect
```

The command above uses keyboard output. Add --no-obc for Bluetooth only or --debug for raw diagnostics in debug.log. Use --discover to find controller addresses. The legacy single-connection CLI mode remains available without --dual; use --no-auto-reset to disable its idle reset. --refresh-handshake is experimental and did not prevent the observed authorization timeout. --fast-reconnect enables Windows GATT cache reuse; the actual speed improvement still needs hardware validation.

## Build executable

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The script installs dependencies, runs unit tests and builds dist/ZwiftController.exe. The executable stores config.json in %LOCALAPPDATA%/ZwiftController; source mode uses the repository config.json. Move the executable to a permanent location before enabling Windows autostart. v0.7.0 GUI and executable require Windows validation.

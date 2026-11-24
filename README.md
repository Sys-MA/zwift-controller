# Zwift Controller

Experimental v0.5.1 Windows bridge. Left Click supplies all ten buttons; right Click connects temporarily for battery reads without a handshake. Hardware validation required. OpenBikeControl v1 TCP/mDNS output is implemented experimentally.

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m unittest discover -s tests -v
py -3.12 zwift_controller.py
```

The local config.json is generated using the development pair addresses. For another pair, run `py -3.12 zwift_controller.py --discover` and edit config.json with the correct left/right addresses. Battery interval defaults to 900 seconds. Failed right battery reads retry after 60 seconds. Use `--no-right-battery` to test the left connection alone. Ctrl+C stops the program. QuickEdit is disabled temporarily when supported by the Windows console.

## MyWhoosh

Start the bridge, wake the controllers, then open the MyWhoosh pairing screen and select the OpenBikeControl icon. Choose the advertised Zwift Controller device. Allow Python on trusted private networks in Windows Firewall if prompted. The server uses TCP port 8765. Use `--obc-host 192.168.1.10` (replace with your actual local IPv4) if the automatic interface selection is wrong. Use `--no-obc` for Bluetooth-only diagnostics. App-specific button support and TCP interoperability still need hardware testing. Haptic commands are accepted but not acted on.

## Keyboard fallback (Windows)

Run `py -3.12 zwift_controller.py --keyboard` to use SendInput instead of OpenBikeControl. Connect the physical KICKR directly to MyWhoosh and keep MyWhoosh in the foreground. PLUS sends K (gear up), MOINS sends I (gear down), as confirmed on the physical keyboard. Other defaults are LEFT/RIGHT/UP/DOWN, A=ENTER, B=ESC, Y=TAB, Z=SPACE; their game actions are not yet validated. Each press sends one key tap, without repeat. The program checks the foreground executable before each tap and does not activate windows. Configure individual buttons using a `keyboard_mapping` object in the local config.json, e.g. `{"PLUS": "K", "MOINS": "I", "Y": null}` to disable Y. A null value disables any mapped button. Do not run both output modes together. Run at the same Windows privilege level as MyWhoosh.

# Zwift Controller

Experimental v0.4.1 Windows bridge. Left Click supplies all ten buttons; right Click connects temporarily for battery reads without a handshake. Hardware validation required. MyWhoosh output is not implemented yet.

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m unittest discover -s tests -v
py -3.12 zwift_controller.py
```

The local config.json is generated using the development pair addresses. For another pair, run `py -3.12 zwift_controller.py --discover` and edit config.json with the correct left/right addresses. Battery interval defaults to 900 seconds. Failed right battery reads retry after 60 seconds. Use `--no-right-battery` to test the left connection alone. Ctrl+C stops the program. QuickEdit is disabled temporarily when supported by the Windows console.

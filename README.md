# Zwift Controller

Experimental Zwift Click V2 Bluetooth bridge for Windows. Version 0.4 detects controllers by name, identifies their side on button press, and reconnects automatically. MyWhoosh output is not implemented yet.

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m unittest discover -s tests
py -3.12 zwift_controller.py
```

Wake both controllers and press a button on each. Ctrl+C stops the program.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3.12 -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
py -3.12 -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
py -3.12 bike_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
py -3.12 -m PyInstaller --noconfirm --clean --onefile --windowed --name ZwiftController --icon bike.ico --collect-submodules bleak --collect-submodules zeroconf --hidden-import pystray._win32 desktop.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Host 'Executable: dist\ZwiftController.exe'

$hash = (Get-FileHash -LiteralPath 'dist\ZwiftController.exe' -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath 'dist\ZwiftController.exe.sha256' -Value "$hash  ZwiftController.exe" -Encoding ascii
Write-Host 'Checksum: dist\ZwiftController.exe.sha256'

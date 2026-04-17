$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
pip install -r requirements-windows.txt

pyinstaller `
  --noconsole `
  --onefile `
  --name win-rec-app `
  main.py

Write-Host "Build complete: dist\win-rec-app.exe"

$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
pip install -r requirements-windows.txt

pyinstaller `
  --noconsole `
  --onefile `
  --name QuickAudioRecorder `
  main.py

Write-Host "Build complete: dist\QuickAudioRecorder.exe"

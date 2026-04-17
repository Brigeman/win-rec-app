$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
pip install -r requirements-windows.txt

pyinstaller `
  --noconsole `
  --onefile `
  --collect-data faster_whisper `
  --collect-data onnxruntime `
  --collect-data huggingface_hub `
  --name win-rec-app `
  main.py

Write-Host "Build complete: dist\win-rec-app.exe"

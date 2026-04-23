#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

PYTHON_BIN=".venv/bin/python"
PIP_BIN=".venv/bin/pip"
PYINSTALLER_BIN=".venv/bin/pyinstaller"

"$PYTHON_BIN" -m pip install --upgrade pip
"$PIP_BIN" install -r requirements-macos.txt

rm -rf build dist

"$PYINSTALLER_BIN" \
  --windowed \
  --onedir \
  --collect-data faster_whisper \
  --collect-data onnxruntime \
  --collect-data huggingface_hub \
  --name win-rec-app \
  --osx-bundle-identifier com.brigeman.win-rec-app \
  main.py

APP_PATH="dist/win-rec-app.app"
DMG_PATH="dist/win-rec-app-macos.dmg"
ZIP_PATH="dist/win-rec-app-macos.zip"

if [ ! -d "$APP_PATH" ]; then
  echo "Expected app bundle not found at $APP_PATH" >&2
  exit 1
fi

# Zip preserves bundle structure for direct download option.
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

# DMG gives standard drag-and-drop install UX.
hdiutil create \
  -volname "win-rec-app" \
  -srcfolder "$APP_PATH" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Build complete:"
echo "  App: $APP_PATH"
echo "  Zip: $ZIP_PATH"
echo "  DMG: $DMG_PATH"

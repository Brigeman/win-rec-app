# win rec app (Windows + macOS)

Desktop recorder focused on reliable call capture, microphone capture, and local transcription with separate platform release tracks.

## MVP features

- Floating always-on-top recorder bar with:
  - `REC` / `STOP` / `HIDE`
  - status text
  - timer
  - live audio level meter
- Tray integration:
  - hide panel to tray
  - restore panel via tray `Show Panel`
- Meeting detection prompt (no auto-record):
  - monitors call-related apps/windows and loopback activity
  - shows a top-right prompt when a call is likely
  - opens prompt instantly for Telemost/Zoom/Google Meet app/url contexts
  - uses reduced 3-second audio sustain for Teams contexts
  - user chooses `Record` or `Not now`
- Recording modes:
  - microphone
  - system loopback
  - both (mixed)
- Output saved to `Desktop\\win-rec-app` by default.
- File naming format: `YYYY-MM-DD_HH-mm-ss.wav` (or `.mp3` if selected).
- Optional local transcription with `faster-whisper` to `YYYY-MM-DD_HH-mm-ss.txt`.
- Structured logs:
  - Windows: `%LOCALAPPDATA%\\win-rec-app\\logs\\app.log`
  - macOS: `~/Library/Application Support/win-rec-app/logs/app.log`
- Recording-session logs are also mirrored to the selected output folder as `app.log`.

## Security notes

- No cloud API calls are used by this app code.
- Clipboard copy uses Qt API only (no shell command interpolation).
- For transcription, set **Local files only** in settings (enabled by default) to prevent model downloads.
- Meeting detection only inspects process names, active window title, and audio levels (no tab/content capture).

## Development (Windows)

### 1) Install dependencies

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-windows.txt
```

### 2) Run app

```powershell
python main.py
```

## Development (macOS)

### 1) Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-macos.txt
```

### 2) Configure system loopback

Install and configure a virtual loopback device (for example BlackHole). Without it, system-audio capture will be unavailable.

### 3) Run app

```bash
python main.py
```

## Local faster-whisper setup

Default is `local_files_only=true`, so the model must already exist locally.

If `local_files_only` is disabled, the app can auto-download the selected model to:
`%LOCALAPPDATA%\win-rec-app\models`.

Options:

1. Put model files in a local directory and configure `Model path` in app settings.
2. Or pre-warm model cache on build machine and bundle/cache it for deployment.

If model is missing, recording still works, and transcription will show a visible warning/error.

## Build Windows executable (PyInstaller)

```powershell
.\build_windows.ps1
```

Output will be in `dist\win-rec-app.exe`.

## Build macOS binary (PyInstaller)

```bash
chmod +x ./build_macos.sh
./build_macos.sh
```

Outputs:
- `dist/win-rec-app.app` (native app bundle)
- `dist/win-rec-app-macos.zip` (bundle archive)
- `dist/win-rec-app-macos.dmg` (installer disk image)

Recommended install flow for users:
1. Download `win-rec-app-macos.dmg`
2. Open DMG and drag `win-rec-app.app` into `Applications`
3. Launch from `Applications`/Launchpad

## Release versioning (separate tracks)

- Windows releases use tags: `win-vX.Y.Z`
- macOS releases use tags: `mac-vX.Y.Z`

Each workflow publishes only its own artifact, so platform releases can evolve independently.

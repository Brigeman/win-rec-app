# QuickAudioRecorder (Windows MVP)

Windows-only desktop recorder focused on reliable system loopback capture, microphone capture, and local transcription.

## MVP features

- Floating always-on-top recorder bar with:
  - `REC` / `STOP` / `HIDE`
  - status text
  - timer
  - live audio level meter
- Tray integration:
  - hide panel to tray
  - restore panel via tray `Show Panel`
- Recording modes:
  - microphone
  - system loopback
  - both (mixed)
- Output saved to `Desktop\\CallRecorderMVP` by default.
- File naming format: `YYYY-MM-DD_HH-mm-ss.wav` (or `.mp3` if selected).
- Optional local transcription with `faster-whisper` to `YYYY-MM-DD_HH-mm-ss.txt`.
- Structured logs in `%LOCALAPPDATA%\\QuickAudioRecorder\\logs\\app.log`.

## Security notes

- No cloud API calls are used by this app code.
- Clipboard copy uses Qt API only (no shell command interpolation).
- For transcription, set **Local files only** in settings (enabled by default) to prevent model downloads.

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

## Local faster-whisper setup

Default is `local_files_only=true`, so the model must already exist locally.

Options:

1. Put model files in a local directory and configure `Model path` in app settings.
2. Or pre-warm model cache on build machine and bundle/cache it for deployment.

If model is missing, recording still works, but transcription will fail with a visible error.

## Build single EXE (PyInstaller)

```powershell
.\build_windows.ps1
```

Output will be in `dist\QuickAudioRecorder.exe`.

## GitHub notes

After force-pushing rewritten history, the GitHub `Contributors` block may take some time to refresh.

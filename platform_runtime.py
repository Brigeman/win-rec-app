import os
import platform
import tempfile


APP_DIR_NAME = "win-rec-app"


def current_platform() -> str:
    return platform.system().lower()


def is_windows() -> bool:
    return current_platform() == "windows"


def is_macos() -> bool:
    return current_platform() == "darwin"


def app_support_dir() -> str:
    override = os.getenv("WIN_REC_APP_DATA_DIR", "").strip()
    if override:
        path = os.path.abspath(override)
        os.makedirs(path, exist_ok=True)
        return path

    if is_windows():
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    elif is_macos():
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.getenv("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    path = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        fallback = os.path.join(tempfile.gettempdir(), APP_DIR_NAME)
        os.makedirs(fallback, exist_ok=True)
        return fallback


def logs_dir() -> str:
    path = os.path.join(app_support_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path

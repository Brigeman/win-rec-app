import importlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from audio_backends import create_audio_backend
from platform_runtime import is_macos


def assert_import(name: str):
    module = importlib.import_module(name)
    if module is None:
        raise RuntimeError(f"Failed to import: {name}")


def main():
    if not is_macos():
        print("Skipping macOS smoke on non-macOS runner.")
        return

    assert_import("audio_backends")
    assert_import("macos_presence")
    assert_import("meeting_detection")
    assert_import("gui")

    backend = create_audio_backend()
    devices = backend.list_microphones(include_loopback=True)
    if not isinstance(devices, list):
        raise RuntimeError("Audio backend returned invalid device list.")

    print("macOS smoke checks passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"macOS smoke failed: {exc}", file=sys.stderr)
        raise

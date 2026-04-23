import importlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from platform_runtime import is_windows


def assert_import(name: str):
    module = importlib.import_module(name)
    if module is None:
        raise RuntimeError(f"Failed to import: {name}")


def main():
    if not is_windows():
        print("Skipping Windows smoke on non-Windows runner.")
        return

    assert_import("audio_backends")
    assert_import("meeting_detection")
    assert_import("gui")

    print("Windows smoke checks passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Windows smoke failed: {exc}", file=sys.stderr)
        raise

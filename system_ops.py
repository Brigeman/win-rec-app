import os
import subprocess
from typing import Protocol

from platform_runtime import is_macos, is_windows


class SystemOps(Protocol):
    def open_path(self, path: str):
        ...


class DefaultSystemOps:
    def open_path(self, path: str):
        if is_windows():
            os.startfile(path)  # type: ignore[attr-defined]
            return
        if is_macos():
            subprocess.run(["open", path], check=False)
            return
        subprocess.run(["xdg-open", path], check=False)


def create_system_ops() -> SystemOps:
    return DefaultSystemOps()

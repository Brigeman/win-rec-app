import ctypes
from dataclasses import dataclass
from typing import Dict, Optional, Set

import psutil


@dataclass
class ForegroundWindowInfo:
    process_name: str
    title: str


@dataclass
class PresenceSnapshot:
    running_processes: Set[str]
    foreground: ForegroundWindowInfo


class WindowsPresenceProbe:
    def snapshot(self) -> PresenceSnapshot:
        running = self._running_processes()
        fg = self._foreground_window_info()
        return PresenceSnapshot(running_processes=running, foreground=fg)

    def _running_processes(self) -> Set[str]:
        names: Set[str] = set()
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name:
                names.add(name)
        return names

    def _foreground_window_info(self) -> ForegroundWindowInfo:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return ForegroundWindowInfo(process_name="", title="")

            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value or ""

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_name = self._process_name_by_pid(pid.value) or ""
            return ForegroundWindowInfo(process_name=process_name.lower(), title=title)
        except Exception:
            return ForegroundWindowInfo(process_name="", title="")

    @staticmethod
    def _process_name_by_pid(pid: int) -> Optional[str]:
        if not pid:
            return None
        try:
            proc = psutil.Process(pid)
            return proc.name()
        except Exception:
            return None

import ctypes
import time
from typing import Optional, Set

try:
    import psutil
except Exception:  # pragma: no cover - available in packaged/runtime envs
    psutil = None

from presence_probe import ForegroundWindowInfo, PresenceProbe, PresenceSnapshot
from platform_runtime import is_windows
from windows_process_utils import process_name_for_pid


class WindowsPresenceProbe(PresenceProbe):
    _process_cache_ts: float = 0.0
    _process_cache_names: Set[str] = set()

    def snapshot(self) -> PresenceSnapshot:
        running = self._running_processes()
        fg = self._foreground_window_info()
        return PresenceSnapshot(running_processes=running, foreground=fg)

    def _running_processes(self) -> Set[str]:
        if psutil is None:
            return set()
        now = time.monotonic()
        if now - WindowsPresenceProbe._process_cache_ts < 10.0:
            return set(WindowsPresenceProbe._process_cache_names)
        names: Set[str] = set()
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name:
                names.add(name)
        WindowsPresenceProbe._process_cache_ts = now
        WindowsPresenceProbe._process_cache_names = names
        return set(names)

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
        if is_windows():
            name = process_name_for_pid(pid)
            return name or None
        try:
            if psutil is None:
                return None
            proc = psutil.Process(pid)
            return proc.name()
        except Exception:
            return None

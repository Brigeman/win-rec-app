"""Desktop call detection via Win32 window titles (no UI Automation / COM).

Must run on its own thread, never on the pycaw/COM ``call-session-probe`` thread.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from typing import List, Tuple

from app_logger import get_logger
from desktop_call_profiles import (
    PROFILE_BY_APP_ID,
    meets_window_gate,
    resolve_logical_app_id,
    score_uia_text,
)
from detector_trace import detector_trace_enabled
from detection_rules import DEFAULT_UNIVERSAL_RULES, UniversalCallRules
from platform_runtime import is_windows
from windows_desktop_call_uia_probe import DesktopCallPresence, NullDesktopCallUiaProbe
from windows_process_utils import clear_parent_cache, process_name_for_pid

logger = get_logger()

GW_OWNER = 4


class WindowsDesktopTitleProbe:
    """Background title scan (Win32 only, separate from Core Audio COM)."""

    def __init__(
        self,
        interval_seconds: float = 1.0,
        rules: UniversalCallRules = DEFAULT_UNIVERSAL_RULES,
    ):
        self.interval_seconds = interval_seconds
        self.rules = rules
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._snapshot = DesktopCallPresence(active=False)
        self._windows = bool(is_windows())

    def start(self) -> None:
        if not self._windows:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="desktop-title-probe", daemon=True
        )
        self._thread.start()
        logger.info("desktop_title_probe_start | thread=%s", self._thread.name)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def tick(self) -> None:
        """Manual tick (tests); production uses :meth:`_run`."""

    def snapshot(self) -> DesktopCallPresence:
        with self._lock:
            return DesktopCallPresence(
                active=self._snapshot.active,
                app_id=self._snapshot.app_id,
                display_name=self._snapshot.display_name,
                process_name=self._snapshot.process_name,
                pid=self._snapshot.pid,
                score=self._snapshot.score,
                matched=list(self._snapshot.matched),
                timestamp=self._snapshot.timestamp,
            )

    def _run(self) -> None:
        startup_delay = float(os.environ.get("WINREC_TITLE_PROBE_START_DELAY", "5"))
        if startup_delay > 0:
            time.sleep(startup_delay)
        while not self._stop_event.is_set():
            try:
                clear_parent_cache()
                snap = self._scan_once()
                with self._lock:
                    self._snapshot = snap
                if detector_trace_enabled() and snap.score > 0:
                    logger.info(
                        "title_probe | app=%s | score=%s | matched=%s | pid=%s",
                        snap.app_id,
                        snap.score,
                        ",".join(snap.matched[:8]),
                        snap.pid or "",
                    )
            except Exception:
                logger.exception("desktop_title_probe_failed")
            self._stop_event.wait(self.interval_seconds)

    def _scan_once(self) -> DesktopCallPresence:
        best = DesktopCallPresence(active=False)
        started = time.monotonic()
        count = 0

        for _hwnd, pid, title in _enum_visible_windows():
            if time.monotonic() - started > self.rules.uia_max_scan_seconds:
                break
            count += 1
            if count > self.rules.uia_max_windows:
                break
            if not pid or not title:
                continue

            proc_name = process_name_for_pid(pid)
            app_id = resolve_logical_app_id(pid, proc_name)
            if not app_id:
                continue
            profile = PROFILE_BY_APP_ID.get(app_id)
            if profile is None:
                continue

            if not meets_window_gate(profile, title):
                continue

            score, matched = score_uia_text(profile, title, False, False)
            if score > best.score:
                best = DesktopCallPresence(
                    active=score >= profile.min_score,
                    app_id=profile.app_id,
                    display_name=profile.display_name,
                    process_name=proc_name or profile.app_id,
                    pid=pid,
                    score=score,
                    matched=matched,
                    timestamp=time.time(),
                )

        return best


def _enum_visible_windows() -> List[Tuple[int, int, str]]:
    if not is_windows():
        return []

    user32 = ctypes.windll.user32
    results: List[Tuple[int, int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        try:
            hwnd = int(hwnd)
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, GW_OWNER):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = (buf.value or "").strip()
            if not title:
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            results.append((hwnd, int(pid.value), title))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return []
    return results


def create_desktop_probe():
    """Use :func:`windows_desktop_call_uia_probe.create_desktop_probe` (UIA default)."""
    from windows_desktop_call_uia_probe import create_desktop_probe as _factory

    return _factory()

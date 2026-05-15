"""Desktop call detection via Win32 window titles (no UI Automation / COM).

``uiautomation`` marshals COM into Qt's STA main thread and can freeze or
crash the whole app. This probe only uses ``user32`` window enumeration.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from dataclasses import dataclass, field
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
from windows_process_utils import process_name_for_pid

logger = get_logger()

GW_OWNER = 4


class WindowsDesktopTitleProbe:
    """Cached title scan; ``tick()`` runs on the audio probe thread."""

    def __init__(
        self,
        interval_seconds: float = 1.0,
        rules: UniversalCallRules = DEFAULT_UNIVERSAL_RULES,
    ):
        self.interval_seconds = interval_seconds
        self.rules = rules
        self._lock = threading.Lock()
        self._snapshot = DesktopCallPresence(active=False)
        self._windows = bool(is_windows())
        self._last_tick_ts = 0.0
        self._startup_delay_seconds = 2.0
        self._started_ts = time.monotonic()

    def start(self) -> None:
        if self._windows:
            logger.info("desktop_title_probe_inline | host=call-session-probe")

    def stop(self) -> None:
        return

    def tick(self) -> None:
        if not self._windows:
            return
        now = time.monotonic()
        if now - self._started_ts < self._startup_delay_seconds:
            return
        if now - self._last_tick_ts < self.interval_seconds:
            return
        self._last_tick_ts = now
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

            text_blob = title
            if not meets_window_gate(profile, text_blob):
                continue

            score, matched = score_uia_text(profile, text_blob, False, False)
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
    """Title probe by default; opt-in UIA via ``WINREC_ENABLE_UIA=1``."""
    if not is_windows():
        return NullDesktopCallUiaProbe()
    if os.environ.get("WINREC_ENABLE_UIA", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        from windows_desktop_call_uia_probe import WindowsDesktopCallUiaProbe

        logger.info("desktop_probe_factory | mode=uia")
        return WindowsDesktopCallUiaProbe()
    return WindowsDesktopTitleProbe()

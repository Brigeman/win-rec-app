"""Windows desktop call UI probe (UI Automation + app profiles).

UIA/COM must run on the same thread as Core Audio (``WindowsCallSessionProbe``).
A dedicated UIA thread racing pycaw/comtypes caused access violations and a
frozen tray (mutex still held). ``tick()`` is invoked from the audio probe loop;
``start()``/``stop()`` are lifecycle no-ops when inline mode is active.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

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
from windows_process_utils import process_name_for_pid

logger = get_logger()


def uia_probe_disabled() -> bool:
    return os.environ.get("WINREC_DISABLE_UIA", "").strip() in ("1", "true", "yes")


@dataclass
class DesktopCallPresence:
    active: bool = False
    app_id: str = ""
    display_name: str = ""
    process_name: str = ""
    pid: Optional[int] = None
    score: int = 0
    matched: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class NullDesktopCallUiaProbe:
    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def tick(self) -> None:
        return

    def snapshot(self) -> DesktopCallPresence:
        return DesktopCallPresence(active=False, timestamp=time.time())


class WindowsDesktopCallUiaProbe:
    """Cached UIA scan; call ``tick()`` from the COM probe thread only."""

    def __init__(
        self,
        interval_seconds: float = 1.0,
        rules: UniversalCallRules = DEFAULT_UNIVERSAL_RULES,
    ):
        self.interval_seconds = interval_seconds
        self.rules = rules
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._snapshot = DesktopCallPresence(active=False)
        self._windows = bool(is_windows())
        self._last_error_log_ts = 0.0
        self._last_tick_ts = 0.0
        self._inline_mode = True
        self._consecutive_failures = 0
        self._disabled_until_ts = 0.0

    def start(self) -> None:
        if not self._windows or uia_probe_disabled():
            return
        if self._inline_mode:
            logger.info("desktop_uia_probe_inline | host=call-session-probe")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_thread, name="desktop-call-uia-probe", daemon=True
        )
        self._thread.start()
        logger.info("desktop_uia_probe_start | thread=%s", self._thread.name)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def tick(self) -> None:
        """Run one scan if the interval elapsed (COM thread only)."""
        if not self._windows or uia_probe_disabled():
            return
        if time.monotonic() < self._disabled_until_ts:
            return
        now = time.monotonic()
        if now - self._last_tick_ts < self.interval_seconds:
            return
        self._last_tick_ts = now
        try:
            snap = self._scan_once()
            with self._lock:
                self._snapshot = snap
            self._consecutive_failures = 0
            if detector_trace_enabled() and snap.score > 0:
                logger.info(
                    "uia_probe | app=%s | score=%s | matched=%s | pid=%s",
                    snap.app_id,
                    snap.score,
                    ",".join(snap.matched[:8]),
                    snap.pid or "",
                )
        except Exception:
            self._consecutive_failures += 1
            now_log = time.monotonic()
            if now_log - self._last_error_log_ts >= self.rules.uia_error_log_interval_seconds:
                self._last_error_log_ts = now_log
                logger.exception("desktop_uia_probe_failed")
            if self._consecutive_failures >= 5:
                self._disabled_until_ts = time.monotonic() + 60.0
                logger.warning(
                    "desktop_uia_probe_paused | failures=%s | resume_in=60s",
                    self._consecutive_failures,
                )
                self._consecutive_failures = 0

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

    def _run_thread(self) -> None:
        """Legacy standalone thread (dev only); prefer inline ``tick()``."""
        try:
            import comtypes  # type: ignore

            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            while not self._stop_event.is_set():
                self.tick()
                time.sleep(0.2)
        finally:
            try:
                import comtypes  # type: ignore

                comtypes.CoUninitialize()
            except Exception:
                pass

    def _scan_once(self) -> DesktopCallPresence:
        if not self._windows:
            return DesktopCallPresence(active=False)

        try:
            import uiautomation as auto  # type: ignore
        except Exception:
            return DesktopCallPresence(active=False)

        best = DesktopCallPresence(active=False)
        started = time.monotonic()
        visited = 0

        try:
            root = auto.GetRootControl()
        except Exception:
            return DesktopCallPresence(active=False)

        try:
            windows = root.GetChildren()
        except Exception:
            return DesktopCallPresence(active=False)

        for win in windows:
            if time.monotonic() - started > self.rules.uia_max_scan_seconds:
                break
            try:
                if hasattr(win, "Exists") and not win.Exists(maxSearchSeconds=0):
                    continue
                pid = int(getattr(win, "ProcessId", 0) or 0)
            except Exception:
                continue
            if not pid:
                continue

            proc_name = process_name_for_pid(pid)
            app_id = resolve_logical_app_id(pid, proc_name)
            if not app_id:
                continue

            profile = PROFILE_BY_APP_ID.get(app_id)
            if profile is None:
                continue

            text_blob = self._collect_text(win, started, [0], 0)
            if not meets_window_gate(profile, text_blob):
                continue

            has_loopback = False
            has_capture = False
            score, matched = score_uia_text(profile, text_blob, has_loopback, has_capture)

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
            visited += 1
            if visited >= self.rules.uia_max_windows:
                break

        return best

    def _collect_text(
        self,
        control,
        started: float,
        counter: List[int],
        depth: int,
    ) -> str:
        if counter[0] >= self.rules.uia_max_nodes:
            return ""
        if time.monotonic() - started > self.rules.uia_max_scan_seconds:
            return ""
        if depth > self.rules.uia_max_depth:
            return ""

        counter[0] += 1
        parts: List[str] = []

        for attr in ("Name", "AutomationId", "ControlTypeName"):
            try:
                val = getattr(control, attr, None) or ""
                if val:
                    parts.append(str(val))
            except Exception:
                pass

        if depth < self.rules.uia_max_depth:
            try:
                children = control.GetChildren()
            except Exception:
                children = ()
            for child in children:
                if counter[0] >= self.rules.uia_max_nodes:
                    break
                if time.monotonic() - started > self.rules.uia_max_scan_seconds:
                    break
                try:
                    if hasattr(child, "Exists") and not child.Exists(maxSearchSeconds=0):
                        continue
                except Exception:
                    continue
                parts.append(
                    self._collect_text(child, started, counter, depth + 1)
                )

        return " ".join(parts)

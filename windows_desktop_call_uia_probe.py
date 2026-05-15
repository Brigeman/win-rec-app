"""Windows desktop call UI probe (UI Automation + app profiles).

UIA runs on a **dedicated** background thread with ``UIAutomationInitializerInThread``.
It must never share a thread with pycaw/comtypes (``WindowsCallSessionProbe``) or
call psutil during scans — use :mod:`windows_process_utils` for process names.
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
from diagnostic_log import diag, diag_exception
from windows_com_gate import com_gate
from windows_process_utils import clear_parent_cache, process_name_for_pid

logger = get_logger()


def uia_probe_enabled() -> bool:
    """UIA is opt-in only — in-process UIA causes native access violations on some PCs."""
    return os.environ.get("WINREC_ENABLE_UIA", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def uia_probe_disabled() -> bool:
    return not uia_probe_enabled()


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

    def snapshot(self) -> DesktopCallPresence:
        return DesktopCallPresence(active=False, timestamp=time.time())


class WindowsDesktopCallUiaProbe:
    """Background UIA scan; ``snapshot()`` reads a lock-protected cache only."""

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
        self._consecutive_failures = 0
        self._disabled_until_ts = 0.0

    def start(self) -> None:
        if not self._windows or not uia_probe_enabled():
            logger.info("desktop_uia_probe_skip | reason=disabled_by_default")
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
        diag(
            "uia_thread_start",
            channel="threads",
            delay_s=os.environ.get("WINREC_UIA_PROBE_START_DELAY", "5"),
        )
        startup_delay = float(os.environ.get("WINREC_UIA_PROBE_START_DELAY", "5"))
        if startup_delay > 0:
            time.sleep(startup_delay)
        try:
            import uiautomation as auto  # type: ignore
        except Exception as exc:
            logger.exception("desktop_uia_probe_import_failed")
            diag_exception("uia_import_failed", exc)
            return

        try:
            with auto.UIAutomationInitializerInThread():
                diag("uia_initializer_ready", channel="probe")
                tick = 0
                while not self._stop_event.is_set():
                    tick += 1
                    if time.monotonic() < self._disabled_until_ts:
                        self._stop_event.wait(self.interval_seconds)
                        continue
                    try:
                        clear_parent_cache()
                        with com_gate("uia_scan"):
                            snap = self._scan_once()
                        with self._lock:
                            self._snapshot = snap
                        self._consecutive_failures = 0
                        if tick <= 3 or tick % 30 == 0:
                            diag(
                                "uia_tick",
                                channel="probe",
                                tick=tick,
                                score=snap.score,
                                app_id=snap.app_id or "",
                            )
                        if detector_trace_enabled() and snap.score > 0:
                            logger.info(
                                "uia_probe | app=%s | score=%s | matched=%s | pid=%s",
                                snap.app_id,
                                snap.score,
                                ",".join(snap.matched[:8]),
                                snap.pid or "",
                            )
                    except Exception as exc:
                        diag_exception("uia_tick_failed", exc, tick=tick)
                        self._consecutive_failures += 1
                        now_log = time.monotonic()
                        if (
                            now_log - self._last_error_log_ts
                            >= self.rules.uia_error_log_interval_seconds
                        ):
                            self._last_error_log_ts = now_log
                            logger.exception("desktop_uia_probe_failed")
                        if self._consecutive_failures >= 5:
                            self._disabled_until_ts = time.monotonic() + 60.0
                            logger.warning(
                                "desktop_uia_probe_paused | failures=%s | resume_in=60s",
                                self._consecutive_failures,
                            )
                            self._consecutive_failures = 0
                    self._stop_event.wait(self.interval_seconds)
        except Exception:
            logger.exception("desktop_uia_probe_thread_exit")

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


def create_desktop_probe():
    """Title probe by default; UIA only when ``WINREC_ENABLE_UIA=1``."""
    if not is_windows():
        return NullDesktopCallUiaProbe()
    if not uia_probe_enabled():
        from windows_desktop_title_probe import WindowsDesktopTitleProbe

        logger.info("desktop_probe_factory | mode=title | reason=WINREC_ENABLE_UIA_not_set")
        return WindowsDesktopTitleProbe()
    try:
        logger.info("desktop_probe_factory | mode=uia | reason=WINREC_ENABLE_UIA=1")
        return WindowsDesktopCallUiaProbe()
    except Exception:
        logger.exception("desktop_probe_factory | mode=uia_failed_using_title")
        from windows_desktop_title_probe import WindowsDesktopTitleProbe

        return WindowsDesktopTitleProbe()

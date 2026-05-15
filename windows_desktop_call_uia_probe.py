"""Windows desktop call UI probe (UI Automation + app profiles)."""

from __future__ import annotations

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

logger = get_logger()

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None



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
    """Background 1 Hz UIA scan; ``snapshot()`` returns cached state only."""

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

    def start(self) -> None:
        if not self._windows:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="desktop-call-uia-probe", daemon=True
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

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                snap = self._scan_once()
                with self._lock:
                    self._snapshot = snap
                if detector_trace_enabled() and snap.score > 0:
                    logger.info(
                        "uia_probe | app=%s | score=%s | matched=%s | pid=%s",
                        snap.app_id,
                        snap.score,
                        ",".join(snap.matched[:8]),
                        snap.pid or "",
                    )
            except Exception:
                now = time.monotonic()
                if now - self._last_error_log_ts >= self.rules.uia_error_log_interval_seconds:
                    self._last_error_log_ts = now
                    logger.exception("desktop_uia_probe_failed")
            time.sleep(self.interval_seconds)

    def _scan_once(self) -> DesktopCallPresence:
        if not self._windows:
            return DesktopCallPresence(active=False)

        try:
            import uiautomation as auto  # type: ignore
        except Exception:
            return DesktopCallPresence(active=False)

        best = DesktopCallPresence(active=False)
        root = auto.GetRootControl()
        started = time.monotonic()
        visited = 0

        for win in root.GetChildren():
            if time.monotonic() - started > self.rules.uia_max_scan_seconds:
                break
            try:
                pid = int(getattr(win, "ProcessId", 0) or 0)
            except Exception:
                continue
            if not pid:
                continue

            proc_name = self._process_name(pid)
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
                for child in control.GetChildren():
                    if counter[0] >= self.rules.uia_max_nodes:
                        break
                    if time.monotonic() - started > self.rules.uia_max_scan_seconds:
                        break
                    parts.append(
                        self._collect_text(child, started, counter, depth + 1)
                    )
            except Exception:
                pass

        return " ".join(parts)

    @staticmethod
    def _process_name(pid: int) -> str:
        if not pid or psutil is None:
            return ""
        try:
            return (psutil.Process(pid).name() or "").lower()
        except Exception:
            return ""


"""Hybrid call/meeting detector.

Composes :class:`UniversalCallDetector` (primary) with
:class:`LegacyMeetingDetector` (fallback). Universal owns the stop /
auto-stop side because legacy cannot produce those signals. The start
branch falls through to legacy whenever the universal detector did not
itself produce a prompt — this matters on Windows builds where pycaw
is unavailable or where Core Audio yields no usable sessions, since
the legacy detector still has the browser URL / window-title path
that worked previously.

Both detectors should be constructed with the same
:class:`LoopbackAudioProbe` so a single loopback capture thread is
shared between strategies. See ``detector_router.create_detector``.
"""

from __future__ import annotations

from typing import Optional

from app_logger import get_logger
from meeting_detection import DetectionDecision, LegacyMeetingDetector
from universal_call_detector import UniversalCallDetector

logger = get_logger()


class HybridDetector:
    """Universal-primary + Legacy-fallback meeting detector.

    Exposes the same ``start/stop/set_cooldown_*/evaluate`` shape as
    its component detectors so callers (``TrayApplication``) can swap
    it in without code changes.
    """

    def __init__(
        self,
        universal: UniversalCallDetector,
        legacy: LegacyMeetingDetector,
    ):
        self.universal = universal
        self.legacy = legacy
        # Tracks which underlying detector produced the most recent
        # decision so log lines fire only on transitions and so
        # ``should_log_decision`` can delegate to the right backend.
        self._last_active: Optional[str] = None

    def start(self) -> None:
        self.universal.start()
        self.legacy.start()

    def stop(self) -> None:
        try:
            self.universal.stop()
        finally:
            self.legacy.stop()

    def set_cooldown_dismiss(self) -> None:
        self.universal.set_cooldown_dismiss()
        self.legacy.set_cooldown_dismiss()

    def set_cooldown_post_stop(self) -> None:
        self.universal.set_cooldown_post_stop()
        self.legacy.set_cooldown_post_stop()

    def evaluate(self, is_recording: bool, mic_rms: float = 0.0) -> DetectionDecision:
        u = self.universal.evaluate(is_recording, mic_rms)

        # Universal always wins for stop / auto-stop: legacy can't
        # produce those signals at all.
        if u.should_prompt_start or u.should_prompt_stop or u.auto_stop:
            self._set_active("universal", reason=u.reason)
            return u

        # Only consult legacy on the start branch (and only while idle —
        # legacy short-circuits during recording anyway, but skipping
        # the call here also avoids unnecessary CPU on the hot path).
        if not is_recording:
            legacy_decision = self.legacy.evaluate(is_recording, mic_rms)
            if legacy_decision.should_prompt_start:
                self._set_active("legacy", reason=legacy_decision.reason)
                return legacy_decision

        # Universal is the source of truth for the idle/no-prompt case
        # so its richer ``reason`` (``no_call_pid`` / ``probe_unavailable``
        # / ``awaiting_sustain`` / ``cooldown_active`` / ...) survives.
        self._set_active("universal_idle", reason=u.reason)
        return u

    def should_log_decision(self, decision: DetectionDecision) -> bool:
        """Delegate to whichever backend produced ``decision``.

        Used by ``TrayApplication.on_detection_decision`` (duck-typed)
        to dedup the per-tick info line.
        """
        if self._last_active == "legacy":
            legacy_should_log = getattr(self.legacy, "should_log_decision", None)
            if callable(legacy_should_log):
                return bool(legacy_should_log(decision))
            return True
        universal_should_log = getattr(self.universal, "should_log_decision", None)
        if callable(universal_should_log):
            return bool(universal_should_log(decision))
        return True

    # --- helpers --------------------------------------------------------

    def _set_active(self, active: str, *, reason: str) -> None:
        # Collapse ``universal_idle`` and ``universal`` into the same
        # logical state for transition detection: we only care about a
        # path flip between Universal and Legacy.
        path = "legacy" if active == "legacy" else "universal"
        previous_path = (
            "legacy" if self._last_active == "legacy" else
            ("universal" if self._last_active in ("universal", "universal_idle") else None)
        )
        if path != previous_path:
            logger.info(
                "hybrid_path | active=%s | reason=%s",
                path,
                reason or "",
            )
        self._last_active = active

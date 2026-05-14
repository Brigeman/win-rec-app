"""Universal call detector.

Detects any third-party call (Teams, Zoom, Discord, Slack huddle,
Telegram, Telemost, browser WebRTC...) by observing that a non-self
process simultaneously holds an Active render and capture session on
the default Windows audio endpoints for at least
``call_start_sustain_seconds``.

Reuses ``LoopbackAudioProbe`` for low-cost loopback peak/RMS sustain,
which acts as the secondary "listener-only / split-PID" signal.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Set

from app_logger import get_logger
from audio_backends import AudioBackend
from call_probe import CallProbe, CallSessionSnapshot
from detection_rules import (
    DEFAULT_RULES,
    DEFAULT_UNIVERSAL_RULES,
    DetectionRuleSet,
    UniversalCallRules,
)
from meeting_detection import DetectionDecision, LoopbackAudioProbe

logger = get_logger()


class UniversalCallDetector:
    """Strategy-equivalent of :class:`LegacyMeetingDetector`.

    Shares the same ``start/stop/set_cooldown_*/evaluate`` shape so
    callers (e.g. ``TrayApplication``) can swap one for the other.
    """

    # Class-level latch so probe_unavailable is logged at INFO once per
    # process lifetime; subsequent occurrences are demoted to DEBUG.
    _probe_unavailable_logged = False

    def __init__(
        self,
        call_probe: CallProbe,
        rules: DetectionRuleSet = DEFAULT_RULES,
        universal_rules: UniversalCallRules = DEFAULT_UNIVERSAL_RULES,
        audio_backend: Optional[AudioBackend] = None,
        loopback_probe: Optional[LoopbackAudioProbe] = None,
    ):
        self.rules = rules
        self.universal = universal_rules
        self.call_probe = call_probe
        self.audio_probe = loopback_probe or LoopbackAudioProbe(
            audio_backend=audio_backend, rules=rules
        )
        self.active_call_pid: Optional[int] = None
        self.active_call_process_name: str = ""
        self.call_last_seen_ts: float = 0.0
        self.call_start_ts: float = 0.0
        self.prompted_pids: Dict[int, float] = {}
        # PID-based cooldown map: pid -> monotonic ts until which we
        # should not re-prompt for that PID.
        self.pid_cooldown_until: Dict[int, float] = {}
        self.last_evaluated_pid: Optional[int] = None
        # Per-instance log-state trackers so INFO lines fire only on
        # state transitions; ticks remain DEBUG only.
        self._logged_candidate_pids: Set[int] = set()
        self._logged_started_pids: Set[int] = set()
        self._tracked_pid_logged_disappear: Set[int] = set()
        self._prev_log_reason: Optional[str] = None
        self._prev_log_should_prompt: Optional[bool] = None

    def start(self) -> None:
        self.call_probe.start()
        self.audio_probe.start()

    def stop(self) -> None:
        try:
            self.call_probe.stop()
        finally:
            self.audio_probe.stop()

    # --- cooldown plumbing the GUI calls --------------------------------

    def set_cooldown_dismiss(self) -> None:
        pid = self.last_evaluated_pid or self.active_call_pid
        if pid:
            self.pid_cooldown_until[pid] = (
                time.time() + self.universal.dismiss_cooldown_seconds
            )
            logger.info(
                "udet_cooldown_set | pid=%s | seconds=%.1f | kind=dismiss",
                pid,
                float(self.universal.dismiss_cooldown_seconds),
            )

    def set_cooldown_post_stop(self) -> None:
        pid = self.active_call_pid or self.last_evaluated_pid
        if pid:
            self.pid_cooldown_until[pid] = (
                time.time() + self.universal.post_stop_cooldown_seconds
            )
            logger.info(
                "udet_cooldown_set | pid=%s | seconds=%.1f | kind=post_stop",
                pid,
                float(self.universal.post_stop_cooldown_seconds),
            )
        # Reset tracking so a fresh post-stop call cycle starts clean.
        self.active_call_pid = None
        self.active_call_process_name = ""
        self.call_start_ts = 0.0
        self.call_last_seen_ts = 0.0
        self._logged_started_pids.clear()
        self._tracked_pid_logged_disappear.clear()

    # --- evaluation -----------------------------------------------------

    def evaluate(self, is_recording: bool, mic_rms: float = 0.0) -> DetectionDecision:
        now = time.time()
        snapshot = self.call_probe.snapshot()
        audio = self.audio_probe.get_activity()

        self._log_tick(snapshot, audio, now)

        if not snapshot.available:
            self._log_probe_unavailable()
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                reason="probe_unavailable",
                debug={"probe_available": 0.0},
            )

        candidate_pid, candidate_state, matched = self._select_call_candidate(
            snapshot, audio
        )
        self.last_evaluated_pid = candidate_pid

        if candidate_pid is not None and candidate_pid not in self._logged_candidate_pids:
            self._logged_candidate_pids.add(candidate_pid)
            logger.info(
                "udet_call_candidate | pid=%s | proc=%s | cap_peak=%.4f | ren_peak=%.4f",
                candidate_pid,
                candidate_state.process_name or "",
                float(candidate_state.capture_peak),
                float(candidate_state.render_peak),
            )

        # --- Stop / auto-stop branch (only meaningful during recording) ----
        if is_recording:
            return self._evaluate_recording(now, snapshot, audio, candidate_pid)

        # --- Start branch -----------------------------------------------
        if candidate_pid is None:
            self.active_call_pid = None
            self.active_call_process_name = ""
            self.call_start_ts = 0.0
            self.call_last_seen_ts = 0.0
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                reason="no_call_pid",
                debug=self._debug_payload(audio, snapshot),
            )

        # Track sustain on the candidate PID.
        if self.active_call_pid != candidate_pid:
            self.active_call_pid = candidate_pid
            self.active_call_process_name = candidate_state.process_name
            self.call_start_ts = candidate_state.since_ts or now
        self.call_last_seen_ts = now

        if self._on_cooldown(candidate_pid, now):
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                matched_rules=matched,
                reason="cooldown_active",
                call_pid=candidate_pid,
                call_process_name=candidate_state.process_name,
                debug=self._debug_payload(audio, snapshot),
            )

        sustain = now - self.call_start_ts
        sustain_ok = sustain >= self.universal.call_start_sustain_seconds
        if not sustain_ok:
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                matched_rules=matched,
                reason="awaiting_sustain",
                call_pid=candidate_pid,
                call_process_name=candidate_state.process_name,
                debug=self._debug_payload(audio, snapshot),
            )

        if candidate_pid not in self._logged_started_pids:
            self._logged_started_pids.add(candidate_pid)
            split_pid = "universal_split_pid_call" in matched
            logger.info(
                "udet_call_started | pid=%s | proc=%s | sustain=%.2f | reason=%s",
                candidate_pid,
                candidate_state.process_name or "",
                sustain,
                "split_pid" if split_pid else "primary",
            )

        if candidate_pid in self.prompted_pids:
            # Already asked the user for this PID; don't pester them.
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                matched_rules=matched,
                reason="already_prompted",
                call_pid=candidate_pid,
                call_process_name=candidate_state.process_name,
                debug=self._debug_payload(audio, snapshot),
            )

        self.prompted_pids[candidate_pid] = now
        logger.info(
            "udet_prompt_start | pid=%s | proc=%s",
            candidate_pid,
            candidate_state.process_name or "",
        )
        return DetectionDecision(
            should_prompt_start=True,
            score=100,
            matched_rules=matched or ["universal_call"],
            reason="call_sustained",
            call_pid=candidate_pid,
            call_process_name=candidate_state.process_name,
            debug=self._debug_payload(audio, snapshot),
        )

    # --- helpers --------------------------------------------------------

    def _evaluate_recording(
        self,
        now: float,
        snapshot: CallSessionSnapshot,
        audio,
        candidate_pid: Optional[int],
    ) -> DetectionDecision:
        # We piggy-back on whichever PID we considered "the call" at start
        # of the recording. Fall back to the current candidate if we lost
        # that PID (e.g. user started recording manually).
        tracked_pid = self.active_call_pid or candidate_pid
        if tracked_pid is None:
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                reason="recording_no_tracked_pid",
                debug=self._debug_payload(audio, snapshot),
            )

        tracked_in_snapshot = tracked_pid in snapshot.active_call_pids
        if tracked_in_snapshot:
            self.call_last_seen_ts = now
            # The tracked PID reappeared after a transient drop; arm the
            # disappear logger again so we re-log the next absence.
            self._tracked_pid_logged_disappear.discard(tracked_pid)
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                reason="recording_call_active",
                call_pid=tracked_pid,
                call_process_name=self.active_call_process_name,
                debug=self._debug_payload(audio, snapshot),
            )

        missing_for = now - max(self.call_last_seen_ts, self.call_start_ts)
        if tracked_pid not in self._tracked_pid_logged_disappear:
            self._tracked_pid_logged_disappear.add(tracked_pid)
            logger.info(
                "udet_call_disappear | pid=%s | proc=%s | seconds_gone=%.2f",
                tracked_pid,
                self.active_call_process_name or "",
                missing_for,
            )
        end_sustained = (
            missing_for >= self.universal.call_end_sustain_seconds
            and audio.sustained_seconds < self.universal.split_pid_loopback_sustain
        )
        if not end_sustained:
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                reason="recording_call_dropping",
                call_pid=tracked_pid,
                call_process_name=self.active_call_process_name,
                debug=self._debug_payload(audio, snapshot, extra={"missing_for": missing_for}),
            )

        logger.info(
            "udet_prompt_stop | pid=%s | proc=%s | action=prompt",
            tracked_pid,
            self.active_call_process_name or "",
        )
        return DetectionDecision(
            should_prompt_start=False,
            should_prompt_stop=True,
            score=0,
            reason="call_ended",
            matched_rules=["universal_call_end"],
            call_pid=tracked_pid,
            call_process_name=self.active_call_process_name,
            debug=self._debug_payload(audio, snapshot, extra={"missing_for": missing_for}),
        )

    def _select_call_candidate(self, snapshot: CallSessionSnapshot, audio):
        """Choose the most plausible in-call PID from a snapshot.

        Primary rule: any non-self PID that holds both render and capture
        active, capture peak >= ``min_capture_peak``, and is not a known
        voice-service negative.

        Secondary "split PID" rule: if no PID satisfies the primary but
        some non-self PID has only capture active while another non-self
        PID has only render active AND the loopback probe reports
        sustained audio, treat the capture-active PID as the call. This
        covers Electron WebRTC clients where capture and render live on
        sibling processes.
        """
        matched: List[str] = []
        primary_pid: Optional[int] = None
        primary_state = None

        for pid, state in snapshot.active_call_pids.items():
            if pid in snapshot.self_pids:
                continue
            name = state.process_name or ""
            if name in self.universal.self_process_names:
                continue
            if name in self.universal.negative_process_names:
                continue
            if (
                state.render_active
                and state.capture_active
                and state.capture_peak >= self.universal.min_capture_peak
            ):
                primary_pid = pid
                primary_state = state
                matched.append("universal_call")
                break

        if primary_pid is not None:
            return primary_pid, primary_state, matched

        # Split-PID listener relax.
        if audio.sustained_seconds >= self.universal.split_pid_loopback_sustain:
            capture_pid = None
            capture_state = None
            has_render = False
            for pid, state in snapshot.active_call_pids.items():
                if pid in snapshot.self_pids:
                    continue
                name = state.process_name or ""
                if name in self.universal.self_process_names:
                    continue
                if name in self.universal.negative_process_names:
                    continue
                if state.capture_active and state.capture_peak >= self.universal.min_capture_peak:
                    capture_pid = pid
                    capture_state = state
                if state.render_active:
                    has_render = True
            if capture_pid is not None and has_render:
                matched.append("universal_split_pid_call")
                return capture_pid, capture_state, matched

        return None, None, matched

    def _on_cooldown(self, pid: int, now: float) -> bool:
        expiry = self.pid_cooldown_until.get(pid, 0.0)
        if expiry <= 0.0:
            return False
        if now >= expiry:
            self.pid_cooldown_until.pop(pid, None)
            return False
        return True

    def _debug_payload(self, audio, snapshot: CallSessionSnapshot, extra=None) -> Dict[str, float]:
        payload = {
            "loopback_rms": audio.rms,
            "loopback_peak": audio.peak,
            "loopback_sustain": audio.sustained_seconds,
            "probe_available": 1.0 if snapshot.available else 0.0,
            "snapshot_pids": float(len(snapshot.active_call_pids)),
        }
        if extra:
            for key, value in extra.items():
                try:
                    payload[key] = float(value)
                except Exception:
                    continue
        return payload

    def should_log_decision(self, decision: DetectionDecision) -> bool:
        """Return True only on detector state transitions.

        Used by the GUI to dedup the per-tick ``meeting_detector | ...``
        info line so the universal detector's INFO stream stays clean.
        """
        key_reason = decision.reason or ""
        key_prompt = bool(decision.should_prompt_start)
        if (
            key_reason == self._prev_log_reason
            and key_prompt == self._prev_log_should_prompt
        ):
            return False
        self._prev_log_reason = key_reason
        self._prev_log_should_prompt = key_prompt
        return True

    def _log_tick(
        self, snapshot: CallSessionSnapshot, audio, now: float
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        active = [
            (
                pid,
                state.process_name or "",
                int(bool(state.capture_active)),
                int(bool(state.render_active)),
                round(float(state.capture_peak), 4),
                round(float(state.render_peak), 4),
                round(max(0.0, now - (state.since_ts or now)), 2),
            )
            for pid, state in snapshot.active_call_pids.items()
        ]
        tracked_age = 0.0
        if self.active_call_pid and self.call_start_ts:
            tracked_age = max(0.0, now - self.call_start_ts)
        logger.debug(
            "udet_tick | active_pids=%s | loop_sustain=%.2f | tracked_pid=%s | tracked_age=%.2f",
            active,
            float(audio.sustained_seconds),
            self.active_call_pid or "",
            tracked_age,
        )

    def _log_probe_unavailable(self) -> None:
        if not UniversalCallDetector._probe_unavailable_logged:
            UniversalCallDetector._probe_unavailable_logged = True
            logger.info("probe_unavailable | strategy=universal")
        else:
            logger.debug("probe_unavailable | strategy=universal | repeat=1")

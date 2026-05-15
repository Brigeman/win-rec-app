"""Universal call detector (call-first: UIA + audio + merge)."""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Set, Tuple

from app_logger import get_logger
from audio_backends import AudioBackend
from call_probe import CallPidState, CallProbe, CallSessionSnapshot
from call_signal import SOURCE_PRIORITY, CallSignal
from desktop_call_profiles import (
    PROFILE_BY_APP_ID,
    all_known_meeting_process_names,
    resolve_logical_app_id,
)
from detection_rules import (
    DEFAULT_RULES,
    DEFAULT_UNIVERSAL_RULES,
    DetectionRuleSet,
    UniversalCallRules,
)
from detector_trace import detector_trace_enabled
from meeting_detection import AudioActivity, DetectionDecision, LoopbackAudioProbe

logger = get_logger()


class UniversalCallDetector:
    """Call-first detector: merges UIA, known-app audio, core audio, split-PID."""

    _probe_unavailable_logged = False

    def __init__(
        self,
        call_probe: CallProbe,
        rules: DetectionRuleSet = DEFAULT_RULES,
        universal_rules: UniversalCallRules = DEFAULT_UNIVERSAL_RULES,
        audio_backend: Optional[AudioBackend] = None,
        loopback_probe: Optional[LoopbackAudioProbe] = None,
        desktop_uia_probe=None,
    ):
        self.rules = rules
        self.universal = universal_rules
        self.call_probe = call_probe
        self.audio_probe = loopback_probe or LoopbackAudioProbe(
            audio_backend=audio_backend, rules=rules
        )
        self.desktop_uia_probe = desktop_uia_probe
        if self.desktop_uia_probe is None:
            self.desktop_uia_probe = self._create_desktop_uia_probe()

        self.active_call_pid: Optional[int] = None
        self.active_call_process_name: str = ""
        self.active_app_id: str = ""
        self.active_signal_source: str = ""
        self.call_last_seen_ts: float = 0.0
        self.call_start_ts: float = 0.0
        self.prompted_keys: Dict[str, float] = {}
        self.cooldown_until: Dict[str, float] = {}
        self.last_evaluated_pid: Optional[int] = None
        self._best_signal_key: str = ""
        self._best_signal_since_ts: float = 0.0
        self._last_positive_call_signal_ts: float = 0.0
        self._last_uia_score: int = 0

        self._logged_candidate_pids: Set[int] = set()
        self._logged_started_keys: Set[str] = set()
        self._tracked_pid_logged_disappear: Set[int] = set()
        self._prev_log_reason: Optional[str] = None
        self._prev_log_should_prompt: Optional[bool] = None

    @staticmethod
    def _create_desktop_uia_probe():
        try:
            from windows_desktop_call_uia_probe import create_desktop_probe

            return create_desktop_probe()
        except Exception:
            from windows_desktop_call_uia_probe import NullDesktopCallUiaProbe

            return NullDesktopCallUiaProbe()

    def start(self) -> None:
        self.call_probe.start()
        self.audio_probe.start()
        # Title/UIA probe must not share the pycaw COM thread (psutil/COM races).
        if self.desktop_uia_probe:
            self.desktop_uia_probe.start()

    def stop(self) -> None:
        try:
            self.call_probe.stop()
        finally:
            try:
                if self.desktop_uia_probe:
                    self.desktop_uia_probe.stop()
            finally:
                self.audio_probe.stop()

    def set_cooldown_dismiss(self) -> None:
        key = self._cooldown_key(
            self.active_app_id,
            self.last_evaluated_pid or self.active_call_pid,
            self.active_signal_source,
        )
        seconds = self.universal.uia_dismiss_cooldown_seconds
        if self.active_signal_source not in ("uia", "known_app_audio"):
            seconds = self.universal.dismiss_cooldown_seconds
        self.cooldown_until[key] = time.time() + seconds
        logger.info(
            "udet_cooldown_set | key=%s | seconds=%.1f | kind=dismiss",
            key,
            float(seconds),
        )

    def set_cooldown_post_stop(self) -> None:
        key = self._cooldown_key(
            self.active_app_id,
            self.active_call_pid or self.last_evaluated_pid,
            self.active_signal_source,
        )
        self.cooldown_until[key] = (
            time.time() + self.universal.post_stop_cooldown_seconds
        )
        logger.info(
            "udet_cooldown_set | key=%s | seconds=%.1f | kind=post_stop",
            key,
            float(self.universal.post_stop_cooldown_seconds),
        )
        self._reset_tracking()

    def _reset_tracking(self) -> None:
        self.active_call_pid = None
        self.active_call_process_name = ""
        self.active_app_id = ""
        self.active_signal_source = ""
        self.call_start_ts = 0.0
        self.call_last_seen_ts = 0.0
        self._best_signal_key = ""
        self._best_signal_since_ts = 0.0
        self._logged_started_keys.clear()
        self._tracked_pid_logged_disappear.clear()

    def evaluate(self, is_recording: bool, mic_rms: float = 0.0) -> DetectionDecision:
        now = time.time()
        snapshot = self.call_probe.snapshot()
        audio = self.audio_probe.get_activity()
        desktop_presence = (
            self.desktop_uia_probe.snapshot()
            if self.desktop_uia_probe
            else None
        )

        self._log_tick(snapshot, audio, now, desktop_presence)

        signals = self._build_signals(snapshot, audio, desktop_presence, now)
        best = self._pick_best_signal(signals)

        if detector_trace_enabled() and best.score > 0:
            logger.info(
                "signal_merge | best=%s | score=%s | source=%s | matched=%s",
                best.app_id or best.process_name,
                best.score,
                best.source,
                ",".join(best.matched[:10]),
            )

        if is_recording:
            return self._evaluate_recording(now, snapshot, audio, best, desktop_presence)

        return self._evaluate_start(now, snapshot, audio, best)

  # --- signal builders ------------------------------------------------

    def _build_signals(
        self,
        snapshot: CallSessionSnapshot,
        audio: AudioActivity,
        desktop_presence,
        now: float,
    ) -> List[CallSignal]:
        signals = [
            self._signal_from_uia(desktop_presence, audio, snapshot, now),
            self._signal_from_known_app_audio(snapshot, audio, now),
            self._signal_from_core_audio(snapshot, audio, now),
            self._signal_from_split_pid(snapshot, audio, now),
        ]
        return [s for s in signals if s.score > 0 or s.active]

    def _signal_from_uia(
        self, presence, audio: AudioActivity, snapshot: CallSessionSnapshot, now: float
    ) -> CallSignal:
        empty = CallSignal(source="uia", since_ts=now)
        if presence is None or not presence.app_id:
            return empty

        profile = PROFILE_BY_APP_ID.get(presence.app_id)
        if profile is None:
            return empty

        has_loopback = self._loopback_active(audio)
        has_capture = self._any_known_capture(snapshot)
        score = int(presence.score)
        matched = list(presence.matched)
        if has_loopback and "loopback_active" not in matched:
            score = min(100, score + 15)
            matched.append("loopback_active")
        if has_capture and "capture_active" not in matched:
            score = min(100, score + 20)
            matched.append("capture_active")

        self._last_uia_score = score
        active = score >= profile.min_score
        if active:
            self._last_positive_call_signal_ts = now

        return CallSignal(
            source="uia",
            app_id=profile.app_id,
            app=profile.display_name,
            process_name=presence.process_name or profile.app_id,
            pid=presence.pid,
            active=active,
            score=score,
            matched=matched,
            since_ts=now,
        )

    def _signal_from_known_app_audio(
        self, snapshot: CallSessionSnapshot, audio: AudioActivity, now: float
    ) -> CallSignal:
        empty = CallSignal(source="known_app_audio", since_ts=now)
        if not snapshot.available:
            return empty
        if audio.sustained_seconds < self.universal.known_app_capture_loopback_sustain:
            return empty

        known = all_known_meeting_process_names()
        best_score = 0
        best: Optional[Tuple[int, CallPidState, str]] = None

        for pid, state in snapshot.active_call_pids.items():
            if pid in snapshot.self_pids:
                continue
            name = state.process_name or ""
            app_id = resolve_logical_app_id(pid, name)
            if not app_id and name not in known:
                continue
            if name in self.universal.negative_process_names:
                continue
            if not state.capture_active:
                continue
            if app_id is None and name not in known:
                continue
            score = 75
            if app_id:
                score = 80
            if score > best_score:
                best_score = score
                best = (pid, state, app_id or "")

        if best is None:
            return empty

        pid, state, app_id = best
        profile = PROFILE_BY_APP_ID.get(app_id) if app_id else None
        return CallSignal(
            source="known_app_audio",
            app_id=app_id,
            app=profile.display_name if profile else state.process_name,
            process_name=state.process_name,
            pid=pid,
            active=True,
            score=best_score,
            matched=["known_app_capture_plus_loopback"],
            since_ts=state.since_ts or now,
        )

    def _signal_from_core_audio(
        self, snapshot: CallSessionSnapshot, audio: AudioActivity, now: float
    ) -> CallSignal:
        empty = CallSignal(source="core_audio", since_ts=now)
        if not snapshot.available:
            return empty

        pid, state, matched = self._select_core_audio_candidate(snapshot)
        if pid is None or state is None:
            return empty

        return CallSignal(
            source="core_audio",
            app_id=resolve_logical_app_id(pid, state.process_name) or "",
            app=state.process_name,
            process_name=state.process_name,
            pid=pid,
            active=True,
            score=85,
            matched=matched,
            since_ts=state.since_ts or now,
        )

    def _signal_from_split_pid(
        self, snapshot: CallSessionSnapshot, audio: AudioActivity, now: float
    ) -> CallSignal:
        empty = CallSignal(source="split_pid", since_ts=now)
        if not snapshot.available:
            return empty
        if audio.sustained_seconds < self.universal.split_pid_loopback_sustain:
            return empty

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

        if capture_pid is None or capture_state is None or not has_render:
            return empty

        app_id = resolve_logical_app_id(capture_pid, capture_state.process_name) or ""
        return CallSignal(
            source="split_pid",
            app_id=app_id,
            app=capture_state.process_name,
            process_name=capture_state.process_name,
            pid=capture_pid,
            active=True,
            score=70,
            matched=["universal_split_pid_call"],
            since_ts=capture_state.since_ts or now,
        )

    def _pick_best_signal(self, signals: List[CallSignal]) -> CallSignal:
        if not signals:
            return CallSignal(source="uia", score=0, since_ts=time.time())
        return max(
            signals,
            key=lambda s: (s.score, SOURCE_PRIORITY.get(s.source, 0)),
        )

    def _select_core_audio_candidate(
        self, snapshot: CallSessionSnapshot
    ) -> Tuple[Optional[int], Optional[CallPidState], List[str]]:
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
                return pid, state, ["universal_call"]
        return None, None, []

    def _any_known_capture(self, snapshot: CallSessionSnapshot) -> bool:
        if not snapshot.available:
            return False
        known = all_known_meeting_process_names()
        for pid, state in snapshot.active_call_pids.items():
            if pid in snapshot.self_pids:
                continue
            if not state.capture_active:
                continue
            name = state.process_name or ""
            if resolve_logical_app_id(pid, name) or name in known:
                return True
        return False

    def _loopback_active(self, audio: AudioActivity) -> bool:
        return (
            audio.sustained_seconds >= self.universal.known_app_capture_loopback_sustain
            or audio.peak >= self.rules.audio_peak_medium
        )

    def _capture_active(self, snapshot: CallSessionSnapshot) -> bool:
        return self._any_known_capture(snapshot)

  # --- start branch ---------------------------------------------------

    def _evaluate_start(
        self,
        now: float,
        snapshot: CallSessionSnapshot,
        audio: AudioActivity,
        best: CallSignal,
    ) -> DetectionDecision:
        threshold = self.universal.uia_prompt_threshold
        signal_key = self._signal_key(best)

        if best.score < threshold or not best.active:
            self._best_signal_key = ""
            self._best_signal_since_ts = 0.0
            if not snapshot.available and best.score == 0:
                self._log_probe_unavailable()
                return DetectionDecision(
                    should_prompt_start=False,
                    score=0,
                    reason="probe_unavailable" if not snapshot.available else "no_call_signal",
                    debug=self._debug_payload(audio, snapshot),
                )
            return DetectionDecision(
                should_prompt_start=False,
                score=best.score,
                reason="no_call_signal",
                matched_rules=best.matched,
                debug=self._debug_payload(audio, snapshot),
            )

        if signal_key != self._best_signal_key:
            self._best_signal_key = signal_key
            self._best_signal_since_ts = best.since_ts or now
        elif best.since_ts and best.since_ts < self._best_signal_since_ts:
            self._best_signal_since_ts = best.since_ts

        sustain = now - self._best_signal_since_ts
        sustain_needed = self.universal.uia_start_sustain_seconds
        if best.source in ("core_audio", "split_pid", "known_app_audio"):
            sustain_needed = self.universal.call_start_sustain_seconds

        if sustain < sustain_needed:
            return DetectionDecision(
                should_prompt_start=False,
                score=best.score,
                matched_rules=best.matched,
                reason="awaiting_sustain",
                call_pid=best.pid,
                call_process_name=best.process_name,
                app_id=best.app_id,
                app_display_name=best.app,
                signal_source=best.source,
                debug=self._debug_payload(audio, snapshot, extra={"sustain": sustain}),
            )

        cooldown_key = self._cooldown_key(best.app_id, best.pid, best.source)
        if self._on_cooldown(cooldown_key, now):
            return DetectionDecision(
                should_prompt_start=False,
                score=best.score,
                matched_rules=best.matched,
                reason="cooldown_active",
                call_pid=best.pid,
                call_process_name=best.process_name,
                app_id=best.app_id,
                app_display_name=best.app,
                signal_source=best.source,
                debug=self._debug_payload(audio, snapshot),
            )

        self.active_call_pid = best.pid
        self.active_call_process_name = best.process_name
        self.active_app_id = best.app_id
        self.active_signal_source = best.source
        self.call_start_ts = best.since_ts or now
        self.call_last_seen_ts = now
        self.last_evaluated_pid = best.pid

        if signal_key not in self._logged_started_keys:
            self._logged_started_keys.add(signal_key)
            logger.info(
                "udet_call_started | source=%s | app=%s | pid=%s | sustain=%.2f",
                best.source,
                best.app_id,
                best.pid or "",
                sustain,
            )

        if signal_key in self.prompted_keys:
            return DetectionDecision(
                should_prompt_start=False,
                score=best.score,
                matched_rules=best.matched,
                reason="already_prompted",
                call_pid=best.pid,
                call_process_name=best.process_name,
                app_id=best.app_id,
                app_display_name=best.app,
                signal_source=best.source,
                debug=self._debug_payload(audio, snapshot),
            )

        self.prompted_keys[signal_key] = now
        self._last_positive_call_signal_ts = now
        logger.info(
            "udet_prompt_start | source=%s | app=%s | pid=%s | score=%s",
            best.source,
            best.app_id,
            best.pid or "",
            best.score,
        )
        return DetectionDecision(
            should_prompt_start=True,
            score=best.score,
            matched_rules=best.matched or [best.source],
            reason="call_sustained",
            call_pid=best.pid,
            call_process_name=best.process_name,
            app_id=best.app_id,
            app_display_name=best.app,
            signal_source=best.source,
            debug=self._debug_payload(audio, snapshot),
        )

  # --- recording branch -----------------------------------------------

    def _evaluate_recording(
        self,
        now: float,
        snapshot: CallSessionSnapshot,
        audio: AudioActivity,
        best: CallSignal,
        desktop_presence,
    ) -> DetectionDecision:
        if self.active_signal_source == "uia":
            return self._evaluate_recording_uia(now, snapshot, audio, best, desktop_presence)

        tracked_pid = self.active_call_pid
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
            self._tracked_pid_logged_disappear.discard(tracked_pid)
            return DetectionDecision(
                should_prompt_start=False,
                score=0,
                reason="recording_call_active",
                call_pid=tracked_pid,
                call_process_name=self.active_call_process_name,
                app_id=self.active_app_id,
                signal_source=self.active_signal_source,
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

        logger.info("udet_prompt_stop | pid=%s | source=%s", tracked_pid, self.active_signal_source)
        return DetectionDecision(
            should_prompt_start=False,
            should_prompt_stop=True,
            score=0,
            reason="call_ended",
            matched_rules=["universal_call_end"],
            call_pid=tracked_pid,
            call_process_name=self.active_call_process_name,
            app_id=self.active_app_id,
            signal_source=self.active_signal_source,
            debug=self._debug_payload(audio, snapshot, extra={"missing_for": missing_for}),
        )

    def _evaluate_recording_uia(
        self,
        now: float,
        snapshot: CallSessionSnapshot,
        audio: AudioActivity,
        best: CallSignal,
        desktop_presence,
    ) -> DetectionDecision:
        uia_score = self._last_uia_score
        if desktop_presence is not None:
            uia_score = desktop_presence.score

        if uia_score >= self.universal.uia_stop_score_threshold:
            self._last_positive_call_signal_ts = now
            return DetectionDecision(
                should_prompt_start=False,
                score=uia_score,
                reason="recording_call_active",
                call_pid=self.active_call_pid,
                call_process_name=self.active_call_process_name,
                app_id=self.active_app_id,
                signal_source="uia",
                debug=self._debug_payload(audio, snapshot),
            )

        loopback_ok = self._loopback_active(audio)
        capture_ok = self._capture_active(snapshot)
        if loopback_ok or capture_ok:
            self._last_positive_call_signal_ts = now
            return DetectionDecision(
                should_prompt_start=False,
                score=uia_score,
                reason="recording_call_active",
                call_pid=self.active_call_pid,
                call_process_name=self.active_call_process_name,
                app_id=self.active_app_id,
                signal_source="uia",
                debug=self._debug_payload(audio, snapshot),
            )

        quiet_for = now - self._last_positive_call_signal_ts
        if quiet_for < self.universal.uia_stop_sustain_seconds:
            return DetectionDecision(
                should_prompt_start=False,
                score=uia_score,
                reason="recording_call_dropping",
                call_pid=self.active_call_pid,
                call_process_name=self.active_call_process_name,
                debug=self._debug_payload(
                    audio, snapshot, extra={"uia_quiet_for": quiet_for}
                ),
            )

        logger.info(
            "udet_prompt_stop | source=uia | app=%s | quiet_for=%.1f",
            self.active_app_id,
            quiet_for,
        )
        return DetectionDecision(
            should_prompt_start=False,
            should_prompt_stop=True,
            score=0,
            reason="call_ended",
            matched_rules=["uia_call_end"],
            call_pid=self.active_call_pid,
            call_process_name=self.active_call_process_name,
            app_id=self.active_app_id,
            signal_source="uia",
            debug=self._debug_payload(audio, snapshot, extra={"uia_quiet_for": quiet_for}),
        )

  # --- helpers --------------------------------------------------------

    @staticmethod
    def _signal_key(signal: CallSignal) -> str:
        return f"{signal.source}|{signal.app_id}|{signal.pid or 0}"

    @staticmethod
    def _cooldown_key(app_id: str, pid: Optional[int], source: str) -> str:
        if app_id:
            return f"app:{app_id}"
        if pid:
            return f"pid:{pid}"
        return f"src:{source}"

    def _on_cooldown(self, key: str, now: float) -> bool:
        expiry = self.cooldown_until.get(key, 0.0)
        if expiry <= 0.0:
            return False
        if now >= expiry:
            self.cooldown_until.pop(key, None)
            return False
        return True

    def _debug_payload(
        self, audio, snapshot: CallSessionSnapshot, extra=None
    ) -> Dict[str, float]:
        payload = {
            "loopback_rms": audio.rms,
            "loopback_peak": audio.peak,
            "loopback_sustain": audio.sustained_seconds,
            "probe_available": 1.0 if snapshot.available else 0.0,
            "snapshot_pids": float(len(snapshot.active_call_pids)),
            "uia_score": float(self._last_uia_score),
        }
        if extra:
            for key, value in extra.items():
                try:
                    payload[key] = float(value)
                except Exception:
                    continue
        return payload

    def should_log_decision(self, decision: DetectionDecision) -> bool:
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

    def _log_tick(self, snapshot, audio, now: float, desktop_presence) -> None:
        if detector_trace_enabled():
            return
        if not logger.isEnabledFor(logging.DEBUG):
            return
        uia_score = desktop_presence.score if desktop_presence else 0
        logger.debug(
            "udet_tick | pids=%s | loop_sustain=%.2f | uia_score=%s",
            len(snapshot.active_call_pids),
            float(audio.sustained_seconds),
            uia_score,
        )

    def _log_probe_unavailable(self) -> None:
        if not UniversalCallDetector._probe_unavailable_logged:
            UniversalCallDetector._probe_unavailable_logged = True
            logger.info("probe_unavailable | strategy=universal")
        else:
            logger.debug("probe_unavailable | strategy=universal | repeat=1")

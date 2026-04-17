import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import soundcard as sc

from app_logger import get_logger
from detection_rules import DEFAULT_RULES, DetectionRuleSet
from windows_presence import PresenceSnapshot, WindowsPresenceProbe


logger = get_logger()


@dataclass
class AudioActivity:
    rms: float = 0.0
    peak: float = 0.0
    sustained_seconds: float = 0.0
    last_update_ts: float = 0.0


@dataclass
class DetectionDecision:
    should_prompt: bool
    score: int
    matched_rules: List[str] = field(default_factory=list)
    context_key: str = ""
    reason: str = ""
    debug: Dict[str, float] = field(default_factory=dict)


class LoopbackAudioProbe:
    def __init__(self, interval_seconds: float = 0.75):
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._activity = AudioActivity()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="meeting-audio-probe", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def get_activity(self) -> AudioActivity:
        with self._lock:
            return AudioActivity(
                rms=self._activity.rms,
                peak=self._activity.peak,
                sustained_seconds=self._activity.sustained_seconds,
                last_update_ts=self._activity.last_update_ts,
            )

    def _run(self):
        while not self._stop_event.is_set():
            try:
                loopback = self._default_loopback_device()
                if not loopback:
                    self._store(0.0, 0.0, 0.0)
                    time.sleep(1.5)
                    continue

                with loopback.recorder(samplerate=44100, channels=2) as rec:
                    sustained = 0.0
                    while not self._stop_event.is_set():
                        data = rec.record(numframes=2048)
                        if data is None or len(data) == 0:
                            rms = 0.0
                            peak = 0.0
                        else:
                            abs_data = np.abs(data)
                            peak = float(np.max(abs_data))
                            rms = float(np.sqrt(np.mean(np.square(data))))
                        if rms >= DEFAULT_RULES.audio_rms_medium or peak >= DEFAULT_RULES.audio_peak_medium:
                            sustained += self.interval_seconds
                        else:
                            sustained = max(0.0, sustained - self.interval_seconds)
                        self._store(rms, peak, sustained)
                        time.sleep(self.interval_seconds)
            except Exception:
                logger.exception("Loopback audio probe error.")
                self._store(0.0, 0.0, 0.0)
                time.sleep(2.0)

    def _store(self, rms: float, peak: float, sustained: float):
        with self._lock:
            self._activity = AudioActivity(
                rms=max(0.0, min(1.0, rms)),
                peak=max(0.0, min(1.0, peak)),
                sustained_seconds=max(0.0, sustained),
                last_update_ts=time.time(),
            )

    @staticmethod
    def _default_loopback_device():
        try:
            default_speaker = sc.default_speaker()
            if not default_speaker:
                return None
            for mic in sc.all_microphones(include_loopback=True):
                if mic.name == default_speaker.name or default_speaker.name in mic.name:
                    return mic
        except Exception:
            return None
        return None


class MeetingDetector:
    def __init__(self, rules: DetectionRuleSet = DEFAULT_RULES):
        self.rules = rules
        self.presence_probe = WindowsPresenceProbe()
        self.audio_probe = LoopbackAudioProbe()
        self.last_meeting_foreground_ts = 0.0
        self.context_cooldown_until: Dict[str, float] = {}
        self.last_prompt_context = ""
        self.last_evaluated_context = ""

    def start(self):
        self.audio_probe.start()

    def stop(self):
        self.audio_probe.stop()

    def set_cooldown_dismiss(self):
        self._set_context_cooldown(self.rules.dismiss_cooldown_seconds)

    def set_cooldown_post_stop(self):
        self._set_context_cooldown(self.rules.post_stop_cooldown_seconds)

    def evaluate(self, is_recording: bool, mic_rms: float = 0.0) -> DetectionDecision:
        now = time.time()
        if is_recording:
            return DetectionDecision(False, 0, reason="recording_active")

        presence = self.presence_probe.snapshot()
        audio = self.audio_probe.get_activity()
        score = 0
        matched: List[str] = []
        fg_title = presence.foreground.title or ""
        fg_proc = (presence.foreground.process_name or "").lower()
        context_key = f"{fg_proc}|{self._short_title(fg_title)}"
        self.last_evaluated_context = context_key

        if self._is_context_on_cooldown(context_key, now):
            return DetectionDecision(False, 0, reason="cooldown_active", context_key=context_key)

        native_running = bool(self.rules.native_meeting_processes & presence.running_processes)
        fg_is_native = fg_proc in self.rules.native_meeting_processes
        fg_is_browser = fg_proc in self.rules.browser_processes

        strong_match = self._matches_any(fg_title, self.rules.strong_meeting_title_patterns)
        domain_match = self._matches_any(fg_title, self.rules.domain_like_patterns)
        strict_context_match = self._matches_any(fg_title, self.rules.strict_meeting_context_patterns)
        negative_browser = self._matches_any(fg_title, self.rules.negative_title_patterns)
        game_match = self._matches_any(fg_title, self.rules.game_title_patterns)

        if fg_is_native:
            needs_meeting_context = fg_proc in self.rules.meeting_context_required_native_processes
            has_meeting_context = strict_context_match
            if needs_meeting_context and not has_meeting_context:
                # Zoom home/auth views should not be treated as active call context.
                score += self.rules.score_weights["native_meeting_background"]
                matched.append("native_meeting_background")
            else:
                score += self.rules.score_weights["native_meeting_foreground"]
                matched.append("native_meeting_foreground")
                self.last_meeting_foreground_ts = now
        elif native_running:
            score += self.rules.score_weights["native_meeting_background"]
            matched.append("native_meeting_background")

        if fg_is_browser and strong_match:
            score += self.rules.score_weights["browser_meeting_title_strong"]
            matched.append("browser_meeting_title_strong")
            self.last_meeting_foreground_ts = now
        if fg_is_browser and domain_match:
            score += self.rules.score_weights["browser_meeting_domain_like"]
            matched.append("browser_meeting_domain_like")
            self.last_meeting_foreground_ts = now

        instant_native_context = (
            fg_is_native
            and fg_proc in self.rules.instant_prompt_native_processes
            and strict_context_match
        )
        instant_browser_context = fg_is_browser and self._matches_any(
            fg_title, self.rules.instant_prompt_browser_patterns
        )
        if fg_is_browser and strict_context_match and (strong_match or domain_match):
            instant_browser_context = True
        instant_prompt_context = instant_native_context or instant_browser_context
        if instant_native_context:
            matched.append("instant_prompt_native_context")
        if instant_browser_context:
            matched.append("instant_prompt_browser_context")

        if now - self.last_meeting_foreground_ts <= self.rules.recent_foreground_seconds:
            score += self.rules.score_weights["recent_foreground_match"]
            matched.append("recent_foreground_match")

        if audio.rms >= self.rules.audio_rms_high or audio.peak >= self.rules.audio_peak_high:
            score += self.rules.score_weights["loopback_voice_activity_high"]
            matched.append("loopback_voice_activity_high")
        elif audio.rms >= self.rules.audio_rms_medium or audio.peak >= self.rules.audio_peak_medium:
            score += self.rules.score_weights["loopback_voice_activity_medium"]
            matched.append("loopback_voice_activity_medium")

        if mic_rms >= 0.015:
            score += self.rules.score_weights["mic_activity_present"]
            matched.append("mic_activity_present")

        if (audio.rms >= self.rules.audio_rms_medium or audio.peak >= self.rules.audio_peak_medium) and mic_rms >= 0.012:
            score += self.rules.score_weights["dual_source_activity"]
            matched.append("dual_source_activity")

        if fg_is_browser and negative_browser:
            score += self.rules.score_weights["browser_non_meeting_title"]
            matched.append("browser_non_meeting_title")
        if game_match:
            score += self.rules.score_weights["game_foreground"]
            matched.append("game_foreground")

        if native_running and audio.sustained_seconds < 1.0 and not (
            fg_is_browser and (strong_match or domain_match or strict_context_match)
        ):
            score += self.rules.score_weights["meeting_app_idle"]
            matched.append("meeting_app_idle")

        if (not native_running and not (fg_is_browser and (strong_match or domain_match))) and (
            audio.rms >= self.rules.audio_rms_medium or audio.peak >= self.rules.audio_peak_medium
        ):
            score += self.rules.score_weights["music_like_audio_only"]
            matched.append("music_like_audio_only")

        is_teams_context = fg_proc in self.rules.teams_processes
        required_sustain = self.rules.audio_sustain_seconds
        if is_teams_context:
            required_sustain = self.rules.teams_audio_sustain_seconds
            matched.append("teams_context")

        should_prompt = False
        reason = "threshold_not_met"
        if context_key != self.last_prompt_context:
            if instant_prompt_context:
                should_prompt = True
                reason = "instant_context"
            elif score >= self.rules.prompt_threshold and audio.sustained_seconds >= required_sustain:
                should_prompt = True
                reason = "threshold_met"

        if should_prompt:
            self.last_prompt_context = context_key

        return DetectionDecision(
            should_prompt=should_prompt,
            score=score,
            matched_rules=matched,
            context_key=context_key,
            reason=reason,
            debug={
                "loopback_rms": audio.rms,
                "loopback_peak": audio.peak,
                "loopback_sustain": audio.sustained_seconds,
                "mic_rms": mic_rms,
                "required_sustain": required_sustain,
                "instant_prompt_context": 1.0 if instant_prompt_context else 0.0,
            },
        )

    def _is_context_on_cooldown(self, context_key: str, now: float) -> bool:
        expiry = self.context_cooldown_until.get(context_key, 0.0)
        if expiry <= 0.0:
            return False
        if now >= expiry:
            self.context_cooldown_until.pop(context_key, None)
            return False
        return True

    def _set_context_cooldown(self, seconds: float):
        context_key = self.last_evaluated_context or self.last_prompt_context
        if not context_key:
            return
        self.context_cooldown_until[context_key] = time.time() + seconds

    @staticmethod
    def _matches_any(text: str, patterns) -> bool:
        if not text:
            return False
        for pat in patterns:
            if pat.search(text):
                return True
        return False

    @staticmethod
    def _short_title(title: str) -> str:
        normalized = " ".join(title.strip().split())
        if len(normalized) > 80:
            return normalized[:80]
        return normalized

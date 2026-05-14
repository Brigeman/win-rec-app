import re
from dataclasses import dataclass, field
from typing import Dict, List, Pattern, Set


@dataclass
class DetectionRuleSet:
    native_meeting_processes: set[str]
    teams_processes: set[str]
    meeting_context_required_native_processes: set[str]
    instant_prompt_native_processes: set[str]
    browser_processes: set[str]
    strong_meeting_title_patterns: List[Pattern[str]]
    domain_like_patterns: List[Pattern[str]]
    strict_meeting_context_patterns: List[Pattern[str]]
    instant_prompt_browser_patterns: List[Pattern[str]]
    negative_title_patterns: List[Pattern[str]]
    game_title_patterns: List[Pattern[str]]
    score_weights: Dict[str, int]
    prompt_threshold: int
    audio_rms_medium: float
    audio_peak_medium: float
    audio_rms_high: float
    audio_peak_high: float
    audio_sustain_seconds: float
    teams_audio_sustain_seconds: float
    recent_foreground_seconds: float
    dismiss_cooldown_seconds: float
    post_stop_cooldown_seconds: float


def _patterns(raw: List[str]) -> List[Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in raw]


DEFAULT_RULES = DetectionRuleSet(
    native_meeting_processes={
        "ms-teams.exe",
        "teams.exe",
        "zoom.exe",
        "telemost.exe",
        "yandextelemost.exe",
    },
    teams_processes={
        "ms-teams.exe",
        "teams.exe",
    },
    meeting_context_required_native_processes={
        "zoom.exe",
        "telemost.exe",
        "yandextelemost.exe",
        "ms-teams.exe",
        "teams.exe",
    },
    instant_prompt_native_processes={
        "zoom.exe",
        "telemost.exe",
        "yandextelemost.exe",
    },
    browser_processes={
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "browser.exe",
        "yandex.exe",
        "brave.exe",
        "opera.exe",
        "safari.exe",
    },
    strong_meeting_title_patterns=_patterns(
        [
            r"microsoft teams",
            r"zoom meeting",
            r"google meet",
            r"\bmeet\b",
            r"телемост",
            r"yandex telemost",
            r"video meeting",
            r"meeting \| microsoft teams",
        ]
    ),
    domain_like_patterns=_patterns(
        [
            r"teams\.microsoft\.com",
            r"meet\.google\.com",
            r"zoom\.us",
            r"app\.zoom\.us",
            r"telemost\.yandex\.ru",
        ]
    ),
    strict_meeting_context_patterns=_patterns(
        [
            r"zoom\.us\/j\/",
            r"zoom\.us\/wc\/join",
            r"(?:^|\s)zoom meeting(?:\s|$)",
            r"(?:^|\s)join (?:a )?meeting(?:\s|$)",
            r"join from zoom workplace app",
            r"waiting room",
            r"meet\.google\.com\/[a-z0-9\-]{6,}",
            r"(?:^|\s)google meet:",
            r"(?:^|\s)meet\s+[–-]\s+[a-z0-9\-]{3,}",
            r"telemost\.yandex\.ru\/j\/[0-9a-z]+",
            r"(?:^|\s)звонок в яндекс телемосте(?:\s|$)",
            r"(?:^|\s)яндекс телемост(?:\s|$)",
            r"(?:^|\s)meeting \| microsoft teams(?:\s|$)",
            r"(?:^|\s)собрание \| microsoft teams(?:\s|$)",
            r"in a meeting",
        ]
    ),
    instant_prompt_browser_patterns=_patterns(
        [
            r"zoom\.us\/j\/",
            r"zoom\.us\/wc\/join",
            r"(?:^|\s)zoom meeting(?:\s|$)",
            r"(?:^|\s)join (?:a )?meeting(?:\s|$)",
            r"join from zoom workplace app",
            r"waiting room",
            r"meet\.google\.com\/[a-z0-9\-]{6,}",
            r"(?:^|\s)meet\s+[–-]\s+[a-z0-9\-]{3,}",
            r"telemost\.yandex\.ru\/j\/[0-9a-z]+",
            r"(?:^|\s)звонок в яндекс телемосте(?:\s|$)",
        ]
    ),
    negative_title_patterns=_patterns(
        [
            r"youtube",
            r"netflix",
            r"spotify",
            r"twitch",
            r"vk music",
            r"music",
            r"video player",
        ]
    ),
    game_title_patterns=_patterns(
        [
            r"steam",
            r"epic games",
            r"riot client",
            r"game",
            r"launcher",
        ]
    ),
    score_weights={
        "native_meeting_foreground": 45,
        "browser_meeting_title_strong": 40,
        "browser_meeting_domain_like": 40,
        "loopback_voice_activity_high": 35,
        "loopback_voice_activity_medium": 25,
        "mic_activity_present": 15,
        "native_meeting_background": 10,
        "recent_foreground_match": 12,
        "dual_source_activity": 15,
        "music_like_audio_only": -25,
        "browser_non_meeting_title": -30,
        "game_foreground": -35,
        "meeting_app_idle": -15,
    },
    prompt_threshold=60,
    audio_rms_medium=0.02,
    audio_peak_medium=0.08,
    audio_rms_high=0.03,
    audio_peak_high=0.12,
    audio_sustain_seconds=6.0,
    teams_audio_sustain_seconds=3.0,
    recent_foreground_seconds=20.0,
    dismiss_cooldown_seconds=600.0,
    post_stop_cooldown_seconds=120.0,
)


@dataclass
class UniversalCallRules:
    """Tunables for the process-audio based universal call detector.

    The detector treats a non-self PID that simultaneously holds an
    Active render AND capture session for ``call_start_sustain_seconds``
    as an ongoing call. Once recording, the same PID disappearing for
    ``call_end_sustain_seconds`` is treated as call end.
    """

    call_start_sustain_seconds: float = 3.0
    call_end_sustain_seconds: float = 8.0
    min_capture_peak: float = 0.002
    instant_prompt_on_call_start: bool = True
    dismiss_cooldown_seconds: float = 30.0
    post_stop_cooldown_seconds: float = 120.0
    # Secondary "listener-only / split PID" path: capture PID and
    # render PID can live on different processes (Electron WebRTC).
    # When loopback has been sustained ``split_pid_loopback_sustain``
    # seconds and there is any non-self capture session, we treat the
    # capture-active PID as the call PID.
    split_pid_loopback_sustain: float = 6.0
    self_process_names: Set[str] = field(
        default_factory=lambda: {
            "win-rec-app.exe",
            "python.exe",
            "pythonw.exe",
        }
    )
    # Windows voice/search services that legitimately hold capture
    # but are not real calls. PIDs whose process name lands here are
    # ignored even if they pass the render+capture predicate.
    negative_process_names: Set[str] = field(
        default_factory=lambda: {
            "searchhost.exe",
            "searchapp.exe",
            "cortana.exe",
            "ai.exe",
            "applicationframehost.exe",
            "winrtaudio.exe",
        }
    )


DEFAULT_UNIVERSAL_RULES = UniversalCallRules()


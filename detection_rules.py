import re
from dataclasses import dataclass
from typing import Dict, List, Pattern


@dataclass
class DetectionRuleSet:
    native_meeting_processes: set[str]
    browser_processes: set[str]
    strong_meeting_title_patterns: List[Pattern[str]]
    domain_like_patterns: List[Pattern[str]]
    negative_title_patterns: List[Pattern[str]]
    game_title_patterns: List[Pattern[str]]
    score_weights: Dict[str, int]
    prompt_threshold: int
    audio_rms_medium: float
    audio_peak_medium: float
    audio_rms_high: float
    audio_peak_high: float
    audio_sustain_seconds: float
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
    browser_processes={
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "browser.exe",
        "yandex.exe",
        "brave.exe",
        "opera.exe",
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
    recent_foreground_seconds=20.0,
    dismiss_cooldown_seconds=600.0,
    post_stop_cooldown_seconds=120.0,
)

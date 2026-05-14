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
    # Hard-suppress patterns: when the foreground title matches any of
    # these (e.g. browser is on a search results page), the detector
    # skips ALL prompts regardless of score. Catches the "user typed
    # 'zoom meeting' into Google" false-positive class.
    suppress_title_patterns: List[Pattern[str]] = field(default_factory=list)


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
            r"meeting \| microsoft teams",
            r"собрание \| microsoft teams",
            r"\| microsoft teams$",
            r"zoom meeting -\s",
            r"zoom workplace",
            r"google meet\b",
            r"телемост\b",
            r"yandex telemost",
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
            # Only real meeting URLs / app-specific room contexts.
            # Search-page titles like "zoom meeting - Google Search"
            # must NOT match; this is what caused the user-reported
            # false positive when searching for "zoom".
            r"zoom\.us\/j\/",
            r"zoom\.us\/wc\/join",
            r"app\.zoom\.us\/wc\/",
            r"join from zoom workplace app",
            r"meet\.google\.com\/[a-z0-9\-]{6,}",
            # Google Meet tab title in an active room:
            #   "Meet \u2013 abc-defg-hij \u2013 Google Chrome".
            # The 3-4-3 dashed ID is unique to a real Meet room and
            # never appears in a search-results title.
            r"(?:^|\s)meet\s+[\u2013\u2014\-]\s+[a-z]{2,4}-[a-z]{3,5}-[a-z]{2,4}(?:\s|$|\W)",
            r"telemost\.yandex\.ru\/j\/[0-9a-z]+",
            r"(?:^|\s)\u0437\u0432\u043e\u043d\u043e\u043a \u0432 \u044f\u043d\u0434\u0435\u043a\u0441 \u0442\u0435\u043b\u0435\u043c\u043e\u0441\u0442\u0435(?:\s|$)",
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
    suppress_title_patterns=_patterns(
        [
            # Search engine result pages — any meeting-looking keyword
            # in the query line falsely triggers strong/instant rules.
            r"- google search$",
            r"- google \u043f\u043e\u0438\u0441\u043a$",  # — Google Поиск
            r"\u2014 \u043f\u043e\u0438\u0441\u043a google$",  # — Поиск Google
            r"\u2014 \u044f\u043d\u0434\u0435\u043a\u0441",  # — Яндекс
            r"\u044f\u043d\u0434\u0435\u043a\u0441\u002e\u0431\u0440\u0430\u0443\u0437\u0435\u0440",  # Яндекс.Браузер home
            r"- bing$",
            r"- duckduckgo$",
            r"\u043f\u043e\u0438\u0441\u043a \u0432 google",  # Поиск в Google
            r"google search",
            # New tab / start pages and chrome internals.
            r"new tab$",
            r"\u043d\u043e\u0432\u0430\u044f \u0432\u043a\u043b\u0430\u0434\u043a\u0430$",  # Новая вкладка
            r"chrome:\/\/",
            r"edge:\/\/",
            r"about:blank",
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


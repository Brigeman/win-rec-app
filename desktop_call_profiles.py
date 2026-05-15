"""Desktop meeting app profiles and generic UIA scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None

WEBVIEW2_PROCESS = "msedgewebview2.exe"


@dataclass(frozen=True)
class DesktopCallAppProfile:
    app_id: str
    display_name: str
    root_processes: Set[str]
    child_processes: Set[str]
    strong_keywords: Set[str]
    medium_keywords: Set[str]
    weak_keywords: Set[str]
    pre_call_only_keywords: Set[str]
    min_score: int = 70
    # Meet PWA: only score chrome/edge when window text matches these.
    window_gate_patterns: Tuple[str, ...] = ()


def _kw(*items: str) -> Set[str]:
    return {k.lower() for k in items}


TEAMS_PROFILE = DesktopCallAppProfile(
    app_id="teams",
    display_name="Microsoft Teams",
    root_processes=_kw("ms-teams.exe", "teams.exe", "msteams.exe"),
    child_processes=_kw("msedgewebview2.exe"),
    strong_keywords=_kw(
        "leave",
        "hang up",
        "end call",
        "покинуть",
        "завершить",
        "завершить звонок",
        "verlassen",
        "auflegen",
    ),
    medium_keywords=_kw(
        "mute",
        "unmute",
        "share",
        "participants",
        "people",
        "raise hand",
        "camera",
        "meeting controls",
        "выключить микрофон",
        "включить микрофон",
        "поделиться",
        "участники",
        "поднять руку",
        "камера",
        "stummschalten",
        "stummschaltung aufheben",
        "teilen",
        "teilnehmer",
        "hand heben",
        "kamera",
    ),
    weak_keywords=_kw(
        "chat",
        "call duration",
        "more actions",
        "чат",
        "дополнительные действия",
        "weitere aktionen",
    ),
    pre_call_only_keywords=_kw(
        "join",
        "join meeting",
        "присоединиться",
        "beitreten",
    ),
)

ZOOM_PROFILE = DesktopCallAppProfile(
    app_id="zoom",
    display_name="Zoom",
    root_processes=_kw("zoom.exe"),
    child_processes=set(),
    strong_keywords=_kw(
        "leave meeting",
        "end meeting",
        "leave",
        "покинуть конференцию",
        "завершить конференцию",
        "покинуть",
        "meeting verlassen",
        "beenden",
    ),
    medium_keywords=_kw(
        "mute",
        "unmute",
        "start video",
        "stop video",
        "share screen",
        "participants",
        "reactions",
        "выключить звук",
        "включить звук",
        "включить видео",
        "остановить видео",
        "демонстрация экрана",
        "участники",
        "stumm schalten",
        "stummschaltung aufheben",
        "bildschirm freigeben",
        "teilnehmer",
    ),
    weak_keywords=_kw("chat", "record", "reactions", "чат", "запись", "реакции"),
    pre_call_only_keywords=_kw(
        "join",
        "join with computer audio",
        "присоединиться",
        "beitreten",
    ),
)

SLACK_PROFILE = DesktopCallAppProfile(
    app_id="slack",
    display_name="Slack",
    root_processes=_kw("slack.exe"),
    child_processes=set(),
    strong_keywords=_kw(
        "leave huddle",
        "leave call",
        "end call",
        "disconnect",
        "покинуть созвон",
        "завершить звонок",
    ),
    medium_keywords=_kw(
        "mute",
        "unmute",
        "share screen",
        "huddle",
        "camera",
        "participants",
        "выключить микрофон",
        "включить микрофон",
        "поделиться экраном",
    ),
    weak_keywords=_kw("chat", "thread", "canvas"),
    pre_call_only_keywords=_kw("start huddle", "join huddle", "присоединиться"),
)

DISCORD_PROFILE = DesktopCallAppProfile(
    app_id="discord",
    display_name="Discord",
    root_processes=_kw("discord.exe"),
    child_processes=set(),
    strong_keywords=_kw(
        "disconnect",
        "voice connected",
        "leave call",
        "отключиться",
        "голосовое подключение",
    ),
    medium_keywords=_kw(
        "mute",
        "unmute",
        "deafen",
        "undeafen",
        "screen",
        "share your screen",
        "video",
        "заглушить",
        "включить микрофон",
        "демонстрация экрана",
    ),
    weak_keywords=_kw("voice", "call", "activity"),
    pre_call_only_keywords=_kw("join voice", "start call"),
)

TELEGRAM_PROFILE = DesktopCallAppProfile(
    app_id="telegram",
    display_name="Telegram",
    root_processes=_kw("telegram.exe"),
    child_processes=set(),
    strong_keywords=_kw(
        "end call",
        "leave call",
        "hang up",
        "завершить звонок",
        "положить трубку",
    ),
    medium_keywords=_kw(
        "mute",
        "unmute",
        "camera",
        "screen share",
        "microphone",
        "микрофон",
        "камера",
        "демонстрация экрана",
    ),
    weak_keywords=_kw(
        "call",
        "voice chat",
        "video chat",
        "звонок",
        "голосовой чат",
        "видеочат",
    ),
    pre_call_only_keywords=_kw("call", "start call", "начать звонок"),
)

WHATSAPP_PROFILE = DesktopCallAppProfile(
    app_id="whatsapp",
    display_name="WhatsApp",
    root_processes=_kw("whatsapp.exe"),
    child_processes=_kw("msedgewebview2.exe"),
    strong_keywords=_kw(
        "end call",
        "hang up",
        "leave call",
        "завершить звонок",
        "положить трубку",
    ),
    medium_keywords=_kw(
        "mute",
        "unmute",
        "camera",
        "video",
        "microphone",
        "микрофон",
        "камера",
        "видео",
    ),
    weak_keywords=_kw(
        "call",
        "voice call",
        "video call",
        "звонок",
        "видеозвонок",
    ),
    pre_call_only_keywords=_kw("start call", "voice call", "video call"),
)

GOOGLE_MEET_PWA_PROFILE = DesktopCallAppProfile(
    app_id="google_meet_pwa",
    display_name="Google Meet",
    root_processes=_kw("chrome.exe", "msedge.exe"),
    child_processes=set(),
    strong_keywords=_kw(
        "leave call",
        "leave meeting",
        "покинуть звонок",
        "покинуть встречу",
        "anruf verlassen",
        "meeting verlassen",
    ),
    medium_keywords=_kw(
        "microphone",
        "camera",
        "present now",
        "raise hand",
        "participants",
        "turn on microphone",
        "turn off microphone",
        "микрофон",
        "камера",
        "показать экран",
        "участники",
        "поднять руку",
    ),
    weak_keywords=_kw("meet", "meeting", "chat"),
    pre_call_only_keywords=_kw("join now", "ask to join", "присоединиться"),
    window_gate_patterns=(
        "meet.google.com",
        "google meet",
    ),
)

DESKTOP_CALL_PROFILES: Tuple[DesktopCallAppProfile, ...] = (
    TEAMS_PROFILE,
    ZOOM_PROFILE,
    SLACK_PROFILE,
    DISCORD_PROFILE,
    TELEGRAM_PROFILE,
    WHATSAPP_PROFILE,
    GOOGLE_MEET_PWA_PROFILE,
)

PROFILE_BY_APP_ID: Dict[str, DesktopCallAppProfile] = {
    p.app_id: p for p in DESKTOP_CALL_PROFILES
}

ROOT_PROCESS_TO_APP_ID: Dict[str, str] = {}
CHILD_PROCESS_TO_APP_ID: Dict[str, str] = {}
for _profile in DESKTOP_CALL_PROFILES:
    for _proc in _profile.root_processes:
        ROOT_PROCESS_TO_APP_ID[_proc] = _profile.app_id
    for _proc in _profile.child_processes:
        CHILD_PROCESS_TO_APP_ID[_proc] = _profile.app_id


def all_known_meeting_process_names() -> Set[str]:
    names: Set[str] = set()
    for profile in DESKTOP_CALL_PROFILES:
        names.update(profile.root_processes)
    return names


def score_uia_text(
    profile: DesktopCallAppProfile,
    text: str,
    has_loopback: bool,
    has_capture: bool,
) -> Tuple[int, List[str]]:
    """Generic UIA scoring for a single app profile."""
    score = 0
    matched: List[str] = []
    blob = (text or "").lower()

    if profile.app_id:
        score += 25
        matched.append(f"app:{profile.app_id}")

    if blob.strip():
        score += 10
        matched.append("visible_window")

    has_strong = False
    has_medium = False

    for keyword in profile.strong_keywords:
        if keyword in blob:
            score += 45
            has_strong = True
            matched.append(f"strong:{keyword}")

    for keyword in profile.medium_keywords:
        if keyword in blob:
            score += 20
            has_medium = True
            matched.append(f"medium:{keyword}")

    for keyword in profile.weak_keywords:
        if keyword in blob:
            score += 5
            matched.append(f"weak:{keyword}")

    has_pre_call = any(k in blob for k in profile.pre_call_only_keywords)
    if has_pre_call and not has_strong and not has_medium:
        score = min(score, 40)
        matched.append("pre_call_only_cap")

    if has_loopback:
        score += 15
        matched.append("loopback_active")

    if has_capture:
        score += 20
        matched.append("capture_active")

    return min(score, 100), matched


def meets_window_gate(profile: DesktopCallAppProfile, window_text: str) -> bool:
    if not profile.window_gate_patterns:
        return True
    blob = (window_text or "").lower()
    return any(gate in blob for gate in profile.window_gate_patterns)


def resolve_logical_app_id(pid: int, process_name: str) -> str | None:
    """Map a process name (or WebView2 child) to a desktop call ``app_id``."""
    from platform_runtime import is_windows
    from windows_process_utils import resolve_app_id_from_process_tree

    name = (process_name or "").lower()
    if name in ROOT_PROCESS_TO_APP_ID:
        return ROOT_PROCESS_TO_APP_ID[name]
    if name != WEBVIEW2_PROCESS or not pid:
        return None
    if is_windows():
        app_id = resolve_app_id_from_process_tree(
            pid, process_name, ROOT_PROCESS_TO_APP_ID
        )
        return app_id or None
    if psutil is None:
        return None
    try:
        for parent in psutil.Process(pid).parents():
            parent_name = (parent.name() or "").lower()
            if parent_name in ROOT_PROCESS_TO_APP_ID:
                return ROOT_PROCESS_TO_APP_ID[parent_name]
    except Exception:
        return None
    return None


def profile_for_process(pid: int, process_name: str) -> DesktopCallAppProfile | None:
    app_id = resolve_logical_app_id(pid, process_name)
    if not app_id:
        return None
    return PROFILE_BY_APP_ID.get(app_id)

import subprocess
from typing import Optional, Set

try:
    import psutil
except Exception:  # pragma: no cover - available in packaged/runtime envs
    psutil = None

from presence_probe import ForegroundWindowInfo, PresenceProbe, PresenceSnapshot


# Normalize common macOS app names to the same ``.exe``-style keys used by
# DEFAULT_RULES so that detection rules match across platforms. Keys here MUST
# be lowercase and end with ``.app``.
MAC_APP_TO_RULE_KEY = {
    "microsoft teams.app": "ms-teams.exe",
    "teams.app": "ms-teams.exe",
    "zoom.app": "zoom.exe",
    "zoom.us.app": "zoom.exe",
    "google chrome.app": "chrome.exe",
    "chrome.app": "chrome.exe",
    "microsoft edge.app": "msedge.exe",
    "firefox.app": "firefox.exe",
    "safari.app": "safari.exe",
    "brave browser.app": "brave.exe",
    "opera.app": "opera.exe",
    "yandex.app": "yandex.exe",
    "yandex browser.app": "yandex.exe",
    "telemost.app": "telemost.exe",
}


def _normalize_mac_process_name(raw_name: str) -> str:
    """Lowercase, ensure ``.app`` suffix, then map to the cross-platform rule key.

    If no mapping exists, the ``.app`` form is returned as a fallback (matches
    the legacy behavior).
    """
    name = (raw_name or "").strip().lower()
    if not name:
        return ""
    if not name.endswith(".app"):
        name = f"{name}.app"
    return MAC_APP_TO_RULE_KEY.get(name, name)


class MacOSPresenceProbe(PresenceProbe):
    def snapshot(self) -> PresenceSnapshot:
        running = self._running_processes()
        fg = self._foreground_window_info()
        return PresenceSnapshot(running_processes=running, foreground=fg)

    def _running_processes(self) -> Set[str]:
        if psutil is None:
            return set()
        names: Set[str] = set()
        try:
            iterator = psutil.process_iter(["name"])
        except Exception:
            return names
        for proc in iterator:
            try:
                raw = (proc.info.get("name") or "").strip().lower()
            except Exception:
                continue
            if not raw:
                continue
            # Keep the raw bsd-style name (e.g. "MicrosoftTeams") AND the
            # rule-mapped key (e.g. "ms-teams.exe") so detection rules can match
            # either representation.
            names.add(raw)
            mapped = _normalize_mac_process_name(raw)
            if mapped:
                names.add(mapped)
        return names

    def _foreground_window_info(self) -> ForegroundWindowInfo:
        script = """
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            set winTitle to ""
            try
                set winTitle to name of front window of frontApp
            end try
            return appName & "||" & winTitle
        end tell
        """
        try:
            output = subprocess.check_output(
                ["osascript", "-e", script],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if "||" in output:
                proc_name, title = output.split("||", 1)
            else:
                proc_name, title = output, ""
            return ForegroundWindowInfo(process_name=self._to_proc_name(proc_name), title=title)
        except Exception:
            return ForegroundWindowInfo(process_name="", title="")

    @staticmethod
    def _to_proc_name(app_name: str) -> str:
        return _normalize_mac_process_name(app_name)

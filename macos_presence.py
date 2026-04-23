import subprocess
from typing import Optional, Set

try:
    import psutil
except Exception:  # pragma: no cover - available in packaged/runtime envs
    psutil = None

from presence_probe import ForegroundWindowInfo, PresenceProbe, PresenceSnapshot


class MacOSPresenceProbe(PresenceProbe):
    def snapshot(self) -> PresenceSnapshot:
        running = self._running_processes()
        fg = self._foreground_window_info()
        return PresenceSnapshot(running_processes=running, foreground=fg)

    def _running_processes(self) -> Set[str]:
        if psutil is None:
            return set()
        names: Set[str] = set()
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name:
                names.add(name)
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
        name = (app_name or "").strip().lower()
        if not name:
            return ""
        if not name.endswith(".app"):
            return f"{name}.app"
        return name

import os
import subprocess
import sys

from PyQt6.QtWidgets import QApplication
from gui import TrayApplication
from app_logger import get_logger, setup_logging
from platform_factory import create_platform_services


def _git_short_sha() -> str:
    """Return the current git short sha, or ``"dev"`` on any failure.

    Subprocess is guarded with a tiny timeout so packaged builds that
    have no .git/ next to them don't slow startup.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            ["git", "-C", here, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        sha = (result.stdout or "").strip()
        return sha if sha else "dev"
    except Exception:
        return "dev"


def main():
    setup_logging()
    logger = get_logger()
    py_version = "{}.{}.{}".format(*sys.version_info[:3])
    logger.info(
        "app_start | version=%s | platform=%s | python=%s | pid=%s",
        _git_short_sha(),
        sys.platform,
        py_version,
        os.getpid(),
    )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    audio_backend, _, hotkey_service, system_ops = create_platform_services()
    tray = TrayApplication(
        app,
        audio_backend=audio_backend,
        hotkey_service=hotkey_service,
        system_ops=system_ops,
    )

    sys.exit(app.exec())

if __name__ == "__main__":
    main()

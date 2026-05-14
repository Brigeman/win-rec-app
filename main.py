import os
import subprocess
import sys
import threading
import traceback

from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
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


def _install_crash_diagnostics(logger):
    """Log uncaught exceptions and Qt criticals to help explain sudden exits."""

    def _excepthook(exc_type, exc, tb):
        try:
            logger.critical(
                "uncaught_exception | type=%s | msg=%s\n%s",
                getattr(exc_type, "__name__", str(exc_type)),
                exc,
                "".join(traceback.format_exception(exc_type, exc, tb)),
            )
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        try:
            logger.critical(
                "thread_uncaught | thread=%s | type=%s | msg=%s\n%s",
                getattr(args.thread, "name", "?"),
                args.exc_type.__name__ if args.exc_type else "?",
                args.exc_value,
                "".join(
                    traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback
                    )
                ),
            )
        except Exception:
            pass

    threading.excepthook = _thread_excepthook

    def _qt_msg_handler(mode, context, message):
        if mode in (
            QtMsgType.QtCriticalMsg,
            QtMsgType.QtFatalMsg,
        ):
            try:
                logger.warning(
                    "qt_message | mode=%s | file=%s | line=%s | msg=%s",
                    int(mode),
                    context.file if context and context.file else "",
                    context.line if context and context.line else "",
                    message,
                )
            except Exception:
                pass

    qInstallMessageHandler(_qt_msg_handler)


def main():
    setup_logging()
    logger = get_logger()
    _install_crash_diagnostics(logger)
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
    app.aboutToQuit.connect(
        lambda: logger.info("app_about_to_quit | reason=qt_event_loop")
    )

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

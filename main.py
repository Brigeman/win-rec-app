import os
import subprocess
import sys
import threading
import traceback

from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
from PyQt6.QtWidgets import QApplication, QMessageBox
from gui import TrayApplication
from app_logger import get_logger, log_session_banner, set_session_id, setup_logging
from platform_factory import create_platform_services
from session_diagnostics import (
    install_session_diagnostics,
    new_session_id,
    read_previous_session,
    write_session_marker,
)
from single_instance import SingleInstanceGuard


def _git_short_sha() -> str:
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
    def _excepthook(exc_type, exc, tb):
        try:
            logger.critical(
                "uncaught_exception | type=%s | msg=%s\n%s",
                getattr(exc_type, "__name__", str(exc_type)),
                exc,
                "".join(traceback.format_exception(exc_type, exc, tb)),
            )
            for handler in logger.handlers:
                try:
                    handler.flush()
                except Exception:
                    pass
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
            for handler in logger.handlers:
                try:
                    handler.flush()
                except Exception:
                    pass
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


def _acquire_single_instance(app: QApplication, logger) -> SingleInstanceGuard | None:
    guard = SingleInstanceGuard()
    if guard.try_acquire():
        return guard
    logger.warning("app_exit_duplicate_instance | reason=already_running")
    QMessageBox.warning(
        None,
        "win rec app",
        "Приложение уже запущено.\n\n"
        "Проверьте иконку в трее (рядом с часами).\n"
        "Если иконка есть, но не реагирует — завершите процесс "
        "«win-rec-app» в диспетчере задач и запустите снова.",
    )
    return None


def main():
    session_id = new_session_id()
    set_session_id(session_id)
    previous = read_previous_session()

    setup_logging()
    logger = get_logger()
    log_session_banner(logger, previous)
    _install_crash_diagnostics(logger)
    install_session_diagnostics(logger, session_id)

    write_session_marker(
        phase="starting",
        session_id=session_id,
        pid=os.getpid(),
        extra={"version": _git_short_sha()},
    )

    py_version = "{}.{}.{}".format(*sys.version_info[:3])
    logger.info(
        "app_start | version=%s | platform=%s | python=%s | pid=%s | session=%s",
        _git_short_sha(),
        sys.platform,
        py_version,
        os.getpid(),
        session_id,
    )

    app = QApplication(sys.argv)
    single_guard = _acquire_single_instance(app, logger)
    if single_guard is None:
        write_session_marker(
            phase="duplicate_instance_exit",
            session_id=session_id,
            pid=os.getpid(),
        )
        return 1

    app.setQuitOnLastWindowClosed(False)

    def _on_about_to_quit():
        logger.info("app_about_to_quit | reason=qt_event_loop | session=%s", session_id)
        write_session_marker(
            phase="qt_about_to_quit",
            session_id=session_id,
            pid=os.getpid(),
        )
        single_guard.release()

    app.aboutToQuit.connect(_on_about_to_quit)

    audio_backend, _, hotkey_service, system_ops = create_platform_services()
    TrayApplication(
        app,
        audio_backend=audio_backend,
        hotkey_service=hotkey_service,
        system_ops=system_ops,
    )

    write_session_marker(
        phase="running",
        session_id=session_id,
        pid=os.getpid(),
    )

    exit_code = app.exec()
    logger.info("app_exec_returned | exit_code=%s | session=%s", exit_code, session_id)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

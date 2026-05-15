"""Session lifecycle markers and crash dumps for post-mortem debugging."""

from __future__ import annotations

import atexit
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional

from diagnostic_log import diag
from platform_runtime import app_support_dir, logs_dir

_MARKER_FILE = "last_session.json"
_CRASH_LOG = "crash.log"


def _marker_path() -> str:
    return os.path.join(app_support_dir(), _MARKER_FILE)


def _crash_log_path() -> str:
    return os.path.join(logs_dir(), _CRASH_LOG)


def read_previous_session() -> Dict[str, Any]:
    try:
        if not os.path.exists(_marker_path()):
            return {}
        with open(_marker_path(), "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def write_session_marker(
    *,
    phase: str,
    session_id: str,
    pid: int,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "phase": phase,
        "session_id": session_id,
        "pid": pid,
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    if extra:
        payload.update(extra)
    diag("session_marker", channel="startup", phase=phase, pid=pid)
    try:
        os.makedirs(os.path.dirname(_marker_path()) or ".", exist_ok=True)
        with open(_marker_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def install_session_diagnostics(logger, session_id: str) -> None:
    """Register crash dump + atexit marker for this process."""

    try:
        import faulthandler

        os.makedirs(os.path.dirname(_crash_log_path()), exist_ok=True)
        crash_fp = open(_crash_log_path(), "a", encoding="utf-8")
        crash_fp.write(
            f"\n--- faulthandler attach | session={session_id} | pid={os.getpid()} ---\n"
        )
        crash_fp.flush()
        faulthandler.enable(file=crash_fp, all_threads=True)
    except Exception:
        pass

    def _atexit() -> None:
        try:
            write_session_marker(
                phase="atexit",
                session_id=session_id,
                pid=os.getpid(),
            )
            logger.info(
                "session_end | session=%s | pid=%s | reason=atexit",
                session_id,
                os.getpid(),
            )
            for handler in logger.handlers:
                try:
                    handler.flush()
                except Exception:
                    pass
        except Exception:
            pass

    atexit.register(_atexit)


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]

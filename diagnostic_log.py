"""Structured multi-file diagnostics for startup and probe debugging."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

from platform_runtime import logs_dir

_DIAG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"
)
_ROTATION_MAX_BYTES = 5 * 1024 * 1024
_ROTATION_BACKUP_COUNT = 3
_SETUP = False

_CHANNELS = {
    "startup": "startup.log",
    "probe": "probe.log",
    "threads": "threads.log",
}


def setup_diagnostic_loggers() -> None:
    global _SETUP
    if _SETUP:
        return
    base = logs_dir()
    os.makedirs(base, exist_ok=True)
    from app_logger import FlushingRotatingFileHandler

    for channel, filename in _CHANNELS.items():
        lg = logging.getLogger(f"winrec.{channel}")
        if lg.handlers:
            continue
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
        path = os.path.join(base, filename)
        handler = FlushingRotatingFileHandler(
            path,
            maxBytes=_ROTATION_MAX_BYTES,
            backupCount=_ROTATION_BACKUP_COUNT,
            encoding="utf-8",
            delay=False,
        )
        handler.setFormatter(logging.Formatter(_DIAG_FORMAT))
        lg.addHandler(handler)
    _SETUP = True


def diagnostic_log_paths() -> Dict[str, str]:
    base = logs_dir()
    paths = {name: os.path.join(base, fname) for name, fname in _CHANNELS.items()}
    paths["lifecycle"] = os.path.join(base, "lifecycle.jsonl")
    paths["app"] = os.path.join(base, "app.log")
    paths["crash"] = os.path.join(base, "crash.log")
    return paths


def _channel_logger(channel: str) -> logging.Logger:
    setup_diagnostic_loggers()
    return logging.getLogger(f"winrec.{channel}")


def _append_lifecycle(record: Dict[str, Any]) -> None:
    try:
        path = diagnostic_log_paths()["lifecycle"]
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def diag(
    event: str,
    *,
    channel: str = "startup",
    level: str = "info",
    **fields: Any,
) -> None:
    """Write one event to lifecycle.jsonl and a channel log file."""
    setup_diagnostic_loggers()
    from app_logger import get_session_id

    payload = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "session": get_session_id(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "event": event,
    }
    if fields:
        payload["fields"] = fields

    _append_lifecycle(payload)

    parts = [f"event={event}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    message = " | ".join(parts)

    logger = _channel_logger(channel if channel in _CHANNELS else "startup")
    log_fn = getattr(logger, level.lower(), logger.info)
    try:
        log_fn(message)
    except Exception:
        logger.info(message)


def diag_exception(event: str, exc: BaseException, **fields: Any) -> None:
    import traceback

    fields = dict(fields)
    fields["exc"] = exc.__class__.__name__
    fields["msg"] = str(exc)
    fields["tb_tail"] = " || ".join(
        traceback.format_exc().strip().splitlines()[-4:]
    )
    diag(event, channel="probe", level="error", **fields)

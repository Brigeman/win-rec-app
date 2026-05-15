"""Main-thread heartbeat so a hung instance can be replaced safely."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from platform_runtime import app_support_dir

# Heartbeat writes are frequent; log to lifecycle only at DEBUG via diag sparingly.
_heartbeat_log_counter = 0

_HEARTBEAT_NAME = "heartbeat.json"
_STALE_SECONDS = 12.0


def _path() -> str:
    return os.path.join(app_support_dir(), _HEARTBEAT_NAME)


def write_heartbeat(*, session_id: str = "") -> None:
    global _heartbeat_log_counter
    _heartbeat_log_counter += 1
    if _heartbeat_log_counter in (1, 2, 12):
        try:
            from diagnostic_log import diag

            diag(
                "heartbeat_write",
                channel="startup",
                count=_heartbeat_log_counter,
            )
        except Exception:
            pass
    payload = {
        "pid": os.getpid(),
        "ts": time.time(),
        "session_id": session_id,
    }
    try:
        os.makedirs(app_support_dir(), exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def read_heartbeat() -> Dict[str, Any]:
    try:
        if not os.path.exists(_path()):
            return {}
        with open(_path(), "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def heartbeat_age_seconds() -> Optional[float]:
    data = read_heartbeat()
    ts = float(data.get("ts") or 0.0)
    if ts <= 0:
        return None
    return max(0.0, time.time() - ts)


def heartbeat_owner_pid() -> int:
    try:
        return int(read_heartbeat().get("pid") or 0)
    except Exception:
        return 0


def is_heartbeat_stale(max_age: float = _STALE_SECONDS) -> bool:
    age = heartbeat_age_seconds()
    if age is None:
        return True
    return age > max_age

"""Serialize COM-heavy work (pycaw + UIA) across background threads."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

from diagnostic_log import diag

_COM_LOCK = threading.RLock()


@contextmanager
def com_gate(owner: str) -> Iterator[None]:
    """Only one COM consumer (UIA or Core Audio) at a time per process."""
    wait_started = time.monotonic()
    _COM_LOCK.acquire()
    waited_ms = int((time.monotonic() - wait_started) * 1000)
    held_started = time.monotonic()
    if waited_ms > 5:
        diag(
            "com_gate_enter",
            channel="probe",
            owner=owner,
            waited_ms=waited_ms,
        )
    try:
        yield
    finally:
        held_ms = int((time.monotonic() - held_started) * 1000)
        diag(
            "com_gate_exit",
            channel="probe",
            owner=owner,
            held_ms=held_ms,
        )
        _COM_LOCK.release()

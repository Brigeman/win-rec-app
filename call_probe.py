"""Platform-agnostic call session probe interface.

`CallProbe` is the read interface consumed by `UniversalCallDetector`.
On Windows it is implemented by `WindowsCallSessionProbe` (pycaw).
On macOS / dev boxes we fall back to `NullCallProbe`, which yields an
empty snapshot so the detector can degrade gracefully.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Protocol, Set


@dataclass
class CallPidState:
    """Per-PID audio session state.

    Booleans reflect whether the PID currently holds an Active render
    (speakers) or capture (mic) session on the default endpoint.
    `*_peak` is the most recent per-session peak level (0.0..1.0).
    `since_ts` is the monotonic timestamp the PID first appeared in the
    aggregated snapshot (used by the detector to compute sustain).
    """

    pid: int
    process_name: str = ""
    render_active: bool = False
    capture_active: bool = False
    render_peak: float = 0.0
    capture_peak: float = 0.0
    since_ts: float = 0.0


@dataclass
class CallSessionSnapshot:
    """Aggregated snapshot of all third-party audio sessions on this box."""

    active_call_pids: Dict[int, CallPidState] = field(default_factory=dict)
    self_pids: Set[int] = field(default_factory=set)
    timestamp: float = 0.0
    available: bool = True


class CallProbe(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def snapshot(self) -> CallSessionSnapshot: ...


class NullCallProbe:
    """Probe stub for platforms without Core Audio (macOS / dev).

    Returns an empty snapshot with ``available=False`` so the detector
    can short-circuit to ``probe_unavailable``.
    """

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def snapshot(self) -> CallSessionSnapshot:
        return CallSessionSnapshot(
            active_call_pids={},
            self_pids=set(),
            timestamp=time.time(),
            available=False,
        )

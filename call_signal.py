"""Call detection signals merged by :class:`UniversalCallDetector`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

SOURCE_PRIORITY = {
    "uia": 4,
    "known_app_audio": 3,
    "core_audio": 2,
    "split_pid": 1,
    "legacy": 0,
}


@dataclass
class CallSignal:
    source: str
    app_id: str = ""
    app: str = ""
    process_name: str = ""
    pid: int | None = None
    active: bool = False
    score: int = 0
    matched: List[str] = field(default_factory=list)
    since_ts: float = 0.0

    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source, 0)

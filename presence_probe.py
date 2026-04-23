from dataclasses import dataclass
from typing import Protocol, Set


@dataclass
class ForegroundWindowInfo:
    process_name: str
    title: str


@dataclass
class PresenceSnapshot:
    running_processes: Set[str]
    foreground: ForegroundWindowInfo


class PresenceProbe(Protocol):
    def snapshot(self) -> PresenceSnapshot:
        ...

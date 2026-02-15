from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SupportsStep(Protocol):
    def execute(self) -> str: ...


@dataclass(slots=True)
class ZombieAgent:
    workflow: SupportsStep

    def step(self) -> str:
        return self.workflow.execute()

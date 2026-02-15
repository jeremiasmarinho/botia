from __future__ import annotations

from dataclasses import dataclass
from random import uniform


@dataclass(slots=True)
class ClickPoint:
    x: int
    y: int


class GhostMouse:
    def move_and_click(self, point: ClickPoint) -> float:
        delay = uniform(0.8, 1.5)
        _ = point
        return delay

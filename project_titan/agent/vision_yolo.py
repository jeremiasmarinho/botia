from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DetectionFrame:
    cards: list[str]
    board: list[str]
    pot: float
    stacks: dict[str, float]


class VisionYolo:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def detect(self) -> DetectionFrame:
        return DetectionFrame(cards=[], board=[], pot=0.0, stacks={})

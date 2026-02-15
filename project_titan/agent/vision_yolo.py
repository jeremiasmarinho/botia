"""Legacy YOLO vision stub (superseded by :mod:`tools.vision_tool`).

Retained for backward compatibility with older orchestrator configs.
New code should use :class:`tools.vision_tool.VisionTool` instead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DetectionFrame:
    """Raw detection frame returned by the legacy YOLO interface.

    Attributes:
        cards:  Detected card tokens.
        board:  Detected community cards.
        pot:    Pot size.
        stacks: Per-player stack sizes.
    """

    cards: list[str]
    board: list[str]
    pot: float
    stacks: dict[str, float]


class VisionYolo:
    """Placeholder YOLO detector — returns empty frames."""

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def detect(self) -> DetectionFrame:
        """Return an empty detection frame (stub)."""
        return DetectionFrame(cards=[], board=[], pot=0.0, stacks={})

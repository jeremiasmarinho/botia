from __future__ import annotations

from dataclasses import dataclass

from core.math_engine import MathEngine


@dataclass(slots=True)
class EquityEstimate:
    win_rate: float
    tie_rate: float


class EquityTool:
    def __init__(self) -> None:
        self.engine = MathEngine()

    def estimate(
        self,
        hero_cards: list[str],
        board_cards: list[str],
        dead_cards: list[str],
        opponents: int = 1,
    ) -> EquityEstimate:
        result = self.engine.estimate_equity(hero_cards, board_cards, dead_cards, opponents=opponents)
        return EquityEstimate(win_rate=result.win_rate, tie_rate=result.tie_rate)

"""Monte-Carlo equity estimation tool.

Wraps :class:`core.math_engine.MathEngine` with a simplified interface
for the workflow layer.  Returns an :class:`EquityEstimate` with win/tie
rates ready for threshold comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.math_engine import MathEngine


@dataclass(slots=True)
class EquityEstimate:
    """Result of a Monte-Carlo equity simulation.

    Attributes:
        win_rate: Probability of winning outright in ``[0, 1]``.
        tie_rate: Probability of a split pot in ``[0, 1]``.
    """

    win_rate: float
    tie_rate: float


class EquityTool:
    """Facade over :class:`MathEngine` for equity estimation."""

    def __init__(self) -> None:
        self.engine = MathEngine()

    def estimate(
        self,
        hero_cards: list[str],
        board_cards: list[str],
        dead_cards: list[str],
        opponents: int = 1,
        simulations: int = 10_000,
    ) -> EquityEstimate:
        """Run a Monte-Carlo simulation and return win/tie rates.

        Args:
            hero_cards:  Player's hole cards (2–6 for PLO variants).
            board_cards:  Community cards on the board.
            dead_cards:   Cards known to be out of play.
            opponents:    Number of opponents to simulate against.
            simulations:  Number of Monte-Carlo iterations.

        Returns:
            :class:`EquityEstimate` with computed probabilities.
        """
        result = self.engine.estimate_equity(
            hero_cards,
            board_cards,
            dead_cards,
            simulations=simulations,
            opponents=opponents,
        )
        return EquityEstimate(win_rate=result.win_rate, tie_rate=result.tie_rate)

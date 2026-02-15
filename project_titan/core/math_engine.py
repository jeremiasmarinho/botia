from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class EquityResult:
    win_rate: float
    tie_rate: float
    simulations: int


class MathEngine:
    def estimate_equity(
        self,
        hero_cards: Iterable[str],
        board_cards: Iterable[str],
        dead_cards: Iterable[str],
        simulations: int = 10_000,
    ) -> EquityResult:
        hero_cards_list = [card for card in hero_cards if len(card) >= 2]
        board_cards_list = [card for card in board_cards if len(card) >= 2]
        dead_cards_list = [card for card in dead_cards if len(card) >= 2]

        rank_order = "23456789TJQKA"
        rank_value = {rank: idx + 2 for idx, rank in enumerate(rank_order)}

        hero_strength = sum(rank_value.get(card[0], 0) for card in hero_cards_list)
        board_strength = sum(rank_value.get(card[0], 0) for card in board_cards_list)
        visibility_bonus = min(len(dead_cards_list), 12) * 0.003

        base = 0.18
        strength_component = hero_strength / max(1.0, (6 * 14)) * 0.65
        board_component = min(board_strength / max(1.0, (5 * 14)), 1.0) * 0.08

        win_rate = base + strength_component + board_component + visibility_bonus
        win_rate = max(0.05, min(win_rate, 0.95))

        tie_rate = 0.03 if len(board_cards_list) >= 3 else 0.01
        tie_rate = max(0.0, min(tie_rate, 1.0 - win_rate))

        return EquityResult(win_rate=win_rate, tie_rate=tie_rate, simulations=simulations)

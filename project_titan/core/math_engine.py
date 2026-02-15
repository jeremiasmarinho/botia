from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from random import sample
from typing import Iterable

from treys import Card, Deck, Evaluator


@dataclass(slots=True)
class EquityResult:
    win_rate: float
    tie_rate: float
    simulations: int


class MathEngine:
    @staticmethod
    def _normalize_card(card: str) -> str | None:
        cleaned = card.strip().upper().replace("10", "T")
        if len(cleaned) != 2:
            return None

        rank = cleaned[0]
        suit = cleaned[1].lower()
        if rank not in "23456789TJQKA" or suit not in "CDHScdhs":
            return None
        return f"{rank}{suit}"

    @classmethod
    def _parse_cards(cls, cards: Iterable[str]) -> list[int]:
        parsed: list[int] = []
        seen: set[int] = set()

        for card in cards:
            normalized = cls._normalize_card(card)
            if normalized is None:
                continue

            encoded = Card.new(normalized)
            if encoded in seen:
                continue

            seen.add(encoded)
            parsed.append(encoded)

        return parsed

    @staticmethod
    def _evaluate_omaha_like(evaluator: Evaluator, full_board: list[int], hero_cards: list[int]) -> int:
        if len(hero_cards) < 2:
            return evaluator.evaluate(full_board, hero_cards)

        five_eval = getattr(evaluator, "_five", None)
        if five_eval is None or len(full_board) < 3:
            best_score = None
            for hand_combo in combinations(hero_cards, 2):
                score = evaluator.evaluate(full_board, list(hand_combo))
                if best_score is None or score < best_score:
                    best_score = score
            return best_score if best_score is not None else evaluator.evaluate(full_board, hero_cards[:2])

        best_score = None
        for hand_combo in combinations(hero_cards, 2):
            for board_combo in combinations(full_board, 3):
                score = five_eval(list(hand_combo) + list(board_combo))
                if best_score is None or score < best_score:
                    best_score = score

        return best_score if best_score is not None else evaluator.evaluate(full_board, hero_cards[:2])

    def estimate_equity(
        self,
        hero_cards: Iterable[str],
        board_cards: Iterable[str],
        dead_cards: Iterable[str],
        simulations: int = 10_000,
        opponents: int = 1,
    ) -> EquityResult:
        hero = self._parse_cards(hero_cards)
        board = self._parse_cards(board_cards)
        dead = self._parse_cards(dead_cards)

        if len(hero) < 2:
            return EquityResult(win_rate=0.0, tie_rate=0.0, simulations=0)

        blocked = set(hero + board + dead)
        full_deck = Deck().cards
        evaluator = Evaluator()

        wins = 0
        ties = 0
        runs = 0

        opponents_count = max(1, int(opponents))
        board_needed = max(0, 5 - len(board))
        villain_needed = 2 * opponents_count
        sample_size = board_needed + villain_needed

        for _ in range(max(1, simulations)):
            available = [card for card in full_deck if card not in blocked]
            if len(available) < sample_size:
                break

            sampled = sample(available, sample_size)
            sampled_board = sampled[:board_needed]
            villain_cards = sampled[board_needed:]
            villains = [
                villain_cards[idx * 2 : (idx + 1) * 2]
                for idx in range(opponents_count)
            ]

            full_board = board + sampled_board
            hero_score = self._evaluate_omaha_like(evaluator, full_board, hero)
            villain_scores = [evaluator.evaluate(full_board, villain) for villain in villains]
            best_villain = min(villain_scores)

            if hero_score < best_villain:
                wins += 1
            elif hero_score == best_villain:
                ties += 1
            runs += 1

        if runs == 0:
            return EquityResult(win_rate=0.0, tie_rate=0.0, simulations=0)

        return EquityResult(
            win_rate=wins / runs,
            tie_rate=ties / runs,
            simulations=runs,
        )

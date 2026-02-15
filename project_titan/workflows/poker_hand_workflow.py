from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tools.action_tool import ActionTool
from tools.equity_tool import EquityTool
from tools.vision_tool import VisionTool


class SupportsMemory(Protocol):
    def set(self, key: str, value: Any) -> None: ...

    def get(self, key: str, default: Any = None) -> Any: ...


@dataclass(slots=True)
class PokerHandWorkflow:
    vision: VisionTool
    equity: EquityTool
    action: ActionTool
    memory: SupportsMemory

    @staticmethod
    def _normalize_card(card: str) -> str | None:
        cleaned = card.strip().upper().replace("10", "T")
        if len(cleaned) != 2:
            return None

        rank = cleaned[0]
        suit = cleaned[1].lower()
        if rank not in "23456789TJQKA" or suit not in "cdhs":
            return None
        return f"{rank}{suit}"

    @classmethod
    def _merge_dead_cards(cls, *sources: list[str]) -> list[str]:
        merged: list[str] = []
        for source in sources:
            for card in source:
                normalized = cls._normalize_card(card)
                if normalized is None:
                    continue
                if normalized not in merged:
                    merged.append(normalized)
        return merged

    @staticmethod
    def _street_from_board(board_cards: list[str]) -> str:
        board_count = len(board_cards)
        if board_count >= 5:
            return "river"
        if board_count == 4:
            return "turn"
        if board_count >= 3:
            return "flop"
        return "preflop"

    @staticmethod
    def _select_action(win_rate: float, tie_rate: float, street: str, pot: float, stack: float) -> str:
        thresholds: dict[str, tuple[float, float]] = {
            "preflop": (0.60, 0.38),
            "flop": (0.64, 0.42),
            "turn": (0.67, 0.45),
            "river": (0.70, 0.48),
        }

        raise_threshold, call_threshold = thresholds.get(street, (0.64, 0.42))
        score = win_rate + (tie_rate * 0.5)

        if pot > 0 and stack > 0:
            pressure = min(pot / max(stack, 1e-6), 2.5)
            call_threshold += min(pressure * 0.02, 0.04)
            raise_threshold += min(pressure * 0.01, 0.03)

        if score >= raise_threshold:
            return "raise"
        if score >= call_threshold:
            return "call"
        return "fold"

    def execute(self) -> str:
        snapshot = self.vision.read_table()
        memory_dead_cards = self.memory.get("dead_cards", [])
        if not isinstance(memory_dead_cards, list):
            memory_dead_cards = []

        snapshot_dead_cards = getattr(snapshot, "dead_cards", [])
        if not isinstance(snapshot_dead_cards, list):
            snapshot_dead_cards = []

        dead_cards = self._merge_dead_cards(memory_dead_cards, snapshot_dead_cards)
        visible_cards = {
            *(self._normalize_card(card) for card in snapshot.hero_cards),
            *(self._normalize_card(card) for card in snapshot.board_cards),
        }
        dead_cards = [card for card in dead_cards if card not in visible_cards]
        self.memory.set("dead_cards", dead_cards)

        estimate = self.equity.estimate(snapshot.hero_cards, snapshot.board_cards, dead_cards=dead_cards)
        street = self._street_from_board(snapshot.board_cards)

        if len(snapshot.hero_cards) < 2:
            decision = "wait"
        else:
            decision = self._select_action(
                win_rate=estimate.win_rate,
                tie_rate=estimate.tie_rate,
                street=street,
                pot=snapshot.pot,
                stack=snapshot.stack,
            )

        result = self.action.act(decision)
        self.memory.set(
            "last_decision",
            {
                "hero_cards": snapshot.hero_cards,
                "board_cards": snapshot.board_cards,
                "dead_cards": dead_cards,
                "win_rate": estimate.win_rate,
                "tie_rate": estimate.tie_rate,
                "street": street,
                "pot": snapshot.pot,
                "stack": snapshot.stack,
                "decision": decision,
            },
        )
        return f"win_rate={estimate.win_rate:.2f} {result}"

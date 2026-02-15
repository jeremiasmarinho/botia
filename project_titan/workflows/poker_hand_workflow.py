from __future__ import annotations

from dataclasses import dataclass
import os
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
    def _table_profile() -> str:
        profile = os.getenv("TITAN_TABLE_PROFILE", "normal").strip().lower()
        if profile in {"tight", "aggressive"}:
            return profile
        return "normal"

    @staticmethod
    def _table_position() -> str:
        position = os.getenv("TITAN_TABLE_POSITION", "mp").strip().lower()
        valid_positions = {"utg", "mp", "co", "btn", "sb", "bb"}
        if position in valid_positions:
            return position
        return "mp"

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
    def _pot_odds(pot: float, stack: float) -> float:
        if pot <= 0 or stack <= 0:
            return 0.0
        return pot / max(pot + stack, 1e-6)

    @classmethod
    def _select_action(
        cls,
        win_rate: float,
        tie_rate: float,
        street: str,
        pot: float,
        stack: float,
        information_quality: float,
        table_profile: str,
        table_position: str,
    ) -> tuple[str, float, float]:
        base_thresholds: dict[str, tuple[float, float, float]] = {
            "preflop": (0.40, 0.62, 0.75),
            "flop": (0.44, 0.65, 0.78),
            "turn": (0.47, 0.69, 0.82),
            "river": (0.50, 0.72, 0.85),
        }

        call_threshold, raise_small_threshold, raise_big_threshold = base_thresholds.get(street, (0.44, 0.65, 0.78))
        score = win_rate + (tie_rate * 0.5)
        pot_odds = cls._pot_odds(pot, stack)

        profile_offsets: dict[str, tuple[float, float, float]] = {
            "tight": (0.04, 0.05, 0.05),
            "normal": (0.0, 0.0, 0.0),
            "aggressive": (-0.03, -0.04, -0.04),
        }
        call_offset, raise_small_offset, raise_big_offset = profile_offsets.get(table_profile, (0.0, 0.0, 0.0))
        call_threshold += call_offset
        raise_small_threshold += raise_small_offset
        raise_big_threshold += raise_big_offset

        position_offsets: dict[str, tuple[float, float, float]] = {
            "utg": (0.03, 0.04, 0.04),
            "mp": (0.01, 0.01, 0.01),
            "co": (-0.01, -0.02, -0.02),
            "btn": (-0.03, -0.04, -0.04),
            "sb": (0.02, 0.02, 0.02),
            "bb": (0.0, 0.0, 0.0),
        }
        pos_call_offset, pos_raise_small_offset, pos_raise_big_offset = position_offsets.get(table_position, (0.0, 0.0, 0.0))
        call_threshold += pos_call_offset
        raise_small_threshold += pos_raise_small_offset
        raise_big_threshold += pos_raise_big_offset

        call_threshold += min(pot_odds * 0.35, 0.08)
        raise_small_threshold += min(pot_odds * 0.15, 0.05)
        raise_big_threshold += min(pot_odds * 0.10, 0.04)

        information_penalty = max(0.0, 1.0 - information_quality) * 0.06
        call_threshold += information_penalty
        raise_small_threshold += information_penalty * 0.8
        raise_big_threshold += information_penalty * 0.5

        if score >= raise_big_threshold:
            return "raise_big", score, pot_odds
        if score >= raise_small_threshold:
            return "raise_small", score, pot_odds
        if score >= call_threshold:
            return "call", score, pot_odds
        return "fold", score, pot_odds


    @staticmethod
    def _information_quality(hero_cards: list[str], board_cards: list[str], dead_cards: list[str]) -> float:
        observed = len(hero_cards) + len(board_cards) + len(dead_cards)
        return min(max(observed / 12.0, 0.0), 1.0)

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
        table_profile = self._table_profile()
        table_position = self._table_position()
        info_quality = self._information_quality(snapshot.hero_cards, snapshot.board_cards, dead_cards)
        score = estimate.win_rate + (estimate.tie_rate * 0.5)
        pot_odds = self._pot_odds(snapshot.pot, snapshot.stack)

        if len(snapshot.hero_cards) < 2:
            decision = "wait"
            score = 0.0
            pot_odds = self._pot_odds(snapshot.pot, snapshot.stack)
        else:
            decision, score, pot_odds = self._select_action(
                win_rate=estimate.win_rate,
                tie_rate=estimate.tie_rate,
                street=street,
                pot=snapshot.pot,
                stack=snapshot.stack,
                information_quality=info_quality,
                table_profile=table_profile,
                table_position=table_position,
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
                "score": round(score, 4),
                "pot_odds": round(pot_odds, 4),
                "information_quality": round(info_quality, 4),
                "street": street,
                "table_profile": table_profile,
                "table_position": table_position,
                "pot": snapshot.pot,
                "stack": snapshot.stack,
                "decision": decision,
            },
        )
        return f"win_rate={estimate.win_rate:.2f} {result}"

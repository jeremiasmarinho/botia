from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol

from tools.action_tool import ActionTool
from tools.equity_tool import EquityTool
from tools.rng_tool import RngTool
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
    rng: RngTool

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
    def _opponents_count() -> int:
        raw_value = os.getenv("TITAN_OPPONENTS", "1").strip()
        if not raw_value.isdigit():
            return 1
        return min(max(int(raw_value), 1), 9)

    @staticmethod
    def _simulations_count() -> int:
        raw_value = os.getenv("TITAN_SIMULATIONS", "10000").strip()
        if not raw_value.isdigit():
            return 10_000
        return min(max(int(raw_value), 100), 100_000)

    @staticmethod
    def _dynamic_simulations_enabled() -> bool:
        raw_value = os.getenv("TITAN_DYNAMIC_SIMULATIONS", "0").strip().lower()
        return raw_value in {"1", "true", "yes", "on"}

    @staticmethod
    def _rng_evasion_enabled() -> bool:
        raw_value = os.getenv("TITAN_RNG_EVASION", "1").strip().lower()
        return raw_value in {"1", "true", "yes", "on"}

    @staticmethod
    def _current_opponent(memory: SupportsMemory) -> str:
        memory_value = memory.get("current_opponent", "")
        if isinstance(memory_value, str) and memory_value.strip():
            return memory_value.strip()
        return os.getenv("TITAN_CURRENT_OPPONENT", "").strip()

    @staticmethod
    def _heads_up_obfuscation(memory: SupportsMemory) -> bool:
        """Return True when the HiveBrain flagged a heads-up situation between
        two friendly bots.  In that case, we must play aggressively (never
        check-down) so observers see genuine combat."""
        value = memory.get("heads_up_obfuscation", False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _extract_showdown_events(memory: SupportsMemory) -> list[dict[str, Any]]:
        events = memory.get("showdown_events", [])
        if not isinstance(events, list):
            return []

        normalized_events: list[dict[str, Any]] = []
        for event in events:
            if isinstance(event, dict):
                normalized_events.append(event)
        return normalized_events

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        return default

    @staticmethod
    def _effective_simulations(base_simulations: int, street: str, opponents_count: int, dynamic_enabled: bool) -> int:
        if not dynamic_enabled:
            return base_simulations

        street_multiplier: dict[str, float] = {
            "preflop": 0.40,
            "flop": 0.70,
            "turn": 1.00,
            "river": 1.25,
        }
        multiplier = street_multiplier.get(street, 1.0)
        multiway_boost = 1.0 + min(max(opponents_count - 1, 0) * 0.08, 0.40)
        effective = int(base_simulations * multiplier * multiway_boost)
        return min(max(effective, 100), 100_000)

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

    @staticmethod
    def _spr(pot: float, stack: float) -> float:
        if pot <= 0 or stack <= 0:
            return 99.0
        return stack / max(pot, 1e-6)

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
        opponents_count: int,
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
        spr = cls._spr(pot, stack)

        profile_offsets_by_street: dict[str, dict[str, tuple[float, float, float]]] = {
            "tight": {
                "preflop": (0.05, 0.06, 0.06),
                "flop": (0.04, 0.05, 0.05),
                "turn": (0.04, 0.05, 0.05),
                "river": (0.03, 0.04, 0.04),
            },
            "normal": {
                "preflop": (0.0, 0.0, 0.0),
                "flop": (0.0, 0.0, 0.0),
                "turn": (0.0, 0.0, 0.0),
                "river": (0.0, 0.0, 0.0),
            },
            "aggressive": {
                "preflop": (-0.04, -0.05, -0.05),
                "flop": (-0.03, -0.04, -0.04),
                "turn": (-0.03, -0.04, -0.04),
                "river": (-0.02, -0.03, -0.03),
            },
        }
        profile_offsets = profile_offsets_by_street.get(table_profile, profile_offsets_by_street["normal"])
        call_offset, raise_small_offset, raise_big_offset = profile_offsets.get(street, (0.0, 0.0, 0.0))
        call_threshold += call_offset
        raise_small_threshold += raise_small_offset
        raise_big_threshold += raise_big_offset

        position_offsets_by_street: dict[str, dict[str, tuple[float, float, float]]] = {
            "utg": {
                "preflop": (0.04, 0.05, 0.05),
                "flop": (0.02, 0.03, 0.03),
                "turn": (0.02, 0.03, 0.03),
                "river": (0.01, 0.02, 0.02),
            },
            "mp": {
                "preflop": (0.02, 0.02, 0.02),
                "flop": (0.01, 0.01, 0.01),
                "turn": (0.01, 0.01, 0.01),
                "river": (0.0, 0.0, 0.0),
            },
            "co": {
                "preflop": (-0.02, -0.03, -0.03),
                "flop": (-0.01, -0.02, -0.02),
                "turn": (-0.01, -0.01, -0.01),
                "river": (0.0, -0.01, -0.01),
            },
            "btn": {
                "preflop": (-0.04, -0.05, -0.05),
                "flop": (-0.02, -0.03, -0.03),
                "turn": (-0.02, -0.02, -0.02),
                "river": (-0.01, -0.01, -0.01),
            },
            "sb": {
                "preflop": (0.03, 0.03, 0.03),
                "flop": (0.01, 0.01, 0.01),
                "turn": (0.01, 0.01, 0.01),
                "river": (0.0, 0.0, 0.0),
            },
            "bb": {
                "preflop": (0.0, 0.0, 0.0),
                "flop": (-0.01, -0.01, -0.01),
                "turn": (-0.01, -0.01, -0.01),
                "river": (-0.01, -0.01, -0.01),
            },
        }
        position_offsets = position_offsets_by_street.get(table_position, position_offsets_by_street["mp"])
        pos_call_offset, pos_raise_small_offset, pos_raise_big_offset = position_offsets.get(street, (0.0, 0.0, 0.0))
        call_threshold += pos_call_offset
        raise_small_threshold += pos_raise_small_offset
        raise_big_threshold += pos_raise_big_offset

        multiway_factor = max(0, opponents_count - 1)
        call_threshold += min(multiway_factor * 0.015, 0.07)
        raise_small_threshold += min(multiway_factor * 0.02, 0.10)
        raise_big_threshold += min(multiway_factor * 0.025, 0.12)

        call_threshold += min(pot_odds * 0.35, 0.08)
        raise_small_threshold += min(pot_odds * 0.15, 0.05)
        raise_big_threshold += min(pot_odds * 0.10, 0.04)

        if street in {"turn", "river"} and spr <= 2.5:
            call_threshold -= 0.02
            raise_small_threshold -= 0.02
            raise_big_threshold -= 0.03
        elif street in {"preflop", "flop"} and spr >= 8.0:
            raise_small_threshold += 0.01
            raise_big_threshold += 0.02

        if opponents_count == 1 and table_position in {"co", "btn"}:
            raise_small_threshold -= 0.015
            if street in {"turn", "river"}:
                raise_big_threshold -= 0.015

        call_threshold = min(max(call_threshold, 0.25), 0.90)
        raise_small_threshold = min(max(raise_small_threshold, call_threshold + 0.02), 0.94)
        raise_big_threshold = min(max(raise_big_threshold, raise_small_threshold + 0.03), 0.97)

        information_penalty = max(0.0, 1.0 - information_quality) * 0.06
        call_threshold += information_penalty
        raise_small_threshold += information_penalty * 0.8
        raise_big_threshold += information_penalty * 0.5

        call_threshold = min(max(call_threshold, 0.25), 0.92)
        raise_small_threshold = min(max(raise_small_threshold, call_threshold + 0.02), 0.96)
        raise_big_threshold = min(max(raise_big_threshold, raise_small_threshold + 0.03), 0.99)

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

    def execute(self, snapshot: Any | None = None) -> str:
        snapshot = snapshot if snapshot is not None else self.vision.read_table()
        snapshot_events = getattr(snapshot, "showdown_events", [])
        if not isinstance(snapshot_events, list):
            snapshot_events = []

        memory_events = self._extract_showdown_events(self.memory)
        rng_events = [event for event in snapshot_events if isinstance(event, dict)] + memory_events
        for event in rng_events:
            self.rng.ingest_showdown(event)
        if memory_events:
            self.memory.set("showdown_events", [])

        snapshot_opponent = getattr(snapshot, "current_opponent", "")
        if isinstance(snapshot_opponent, str) and snapshot_opponent.strip():
            self.memory.set("current_opponent", snapshot_opponent.strip())

        flagged_opponents = self.rng.flagged_opponents()
        self.memory.set("rng_super_users", flagged_opponents)

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

        street = self._street_from_board(snapshot.board_cards)
        table_profile = self._table_profile()
        table_position = self._table_position()
        opponents_count = self._opponents_count()
        base_simulations = self._simulations_count()
        dynamic_simulations = self._dynamic_simulations_enabled()
        simulations_count = self._effective_simulations(
            base_simulations=base_simulations,
            street=street,
            opponents_count=opponents_count,
            dynamic_enabled=dynamic_simulations,
        )
        estimate = self.equity.estimate(
            snapshot.hero_cards,
            snapshot.board_cards,
            dead_cards=dead_cards,
            opponents=opponents_count,
            simulations=simulations_count,
        )
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
                opponents_count=opponents_count,
            )

        current_opponent = (
            snapshot_opponent.strip()
            if isinstance(snapshot_opponent, str) and snapshot_opponent.strip()
            else self._current_opponent(self.memory)
        )
        rng_alert = None
        if current_opponent:
            rng_alert = self.rng.should_evade(current_opponent)
            if self._rng_evasion_enabled() and rng_alert.should_evade and decision != "wait":
                decision = "fold"

        # Collusion obfuscation: when two friendly bots are heads-up,
        # never check-down – escalate passive actions to look aggressive.
        hu_obfuscation = self._heads_up_obfuscation(self.memory)
        if hu_obfuscation and decision not in {"wait", "fold"}:
            if decision == "call":
                decision = "raise_small"
            elif decision == "raise_small" and score >= 0.55:
                decision = "raise_big"

        result = self.action.act(decision, street=street)
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
                "opponents": opponents_count,
                "simulations": simulations_count,
                "simulations_base": base_simulations,
                "dynamic_simulations": dynamic_simulations,
                "pot": snapshot.pot,
                "stack": snapshot.stack,
                "decision": decision,
                "rng": {
                    "current_opponent": current_opponent,
                    "flagged_opponents": flagged_opponents,
                    "evasion_enabled": self._rng_evasion_enabled(),
                    "evading": bool(rng_alert.should_evade) if rng_alert is not None else False,
                    "z_score": round(self._to_float(getattr(rng_alert, "z_score", 0.0)), 4),
                    "sample_count": int(getattr(rng_alert, "sample_count", 0)) if rng_alert is not None else 0,
                },
                "heads_up_obfuscation": hu_obfuscation,
            },
        )
        return f"win_rate={estimate.win_rate:.2f} {result}"

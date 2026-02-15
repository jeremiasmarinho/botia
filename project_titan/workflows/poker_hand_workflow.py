"""Poker hand workflow — the decision orchestrator.

Receives a :class:`TableSnapshot` from the vision layer and drives the
full decision pipeline:

1. Ingest showdown events into the RNG auditor.
2. Merge dead cards from memory + vision.
3. Compute equity via Monte-Carlo simulation.
4. Select an action via :func:`thresholds.select_action`.
5. Apply heads-up obfuscation when required.
6. Execute the chosen action and persist the decision to memory.

Environment variables
---------------------
``TITAN_TABLE_PROFILE``          ``tight`` / ``normal`` / ``aggressive``.
``TITAN_TABLE_POSITION``         ``utg`` / ``mp`` / ``co`` / ``btn`` / ``sb`` / ``bb``.
``TITAN_OPPONENTS``              Number of opponents (1–9).
``TITAN_SIMULATIONS``            Base Monte-Carlo simulation count.
``TITAN_DYNAMIC_SIMULATIONS``    ``1`` to scale simulations by street depth.
``TITAN_RNG_EVASION``            ``1`` to fold against flagged opponents.
``TITAN_CURRENT_OPPONENT``       Default opponent identifier (fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from tools.action_tool import ActionTool
from tools.equity_tool import EquityTool
from tools.rng_tool import RngTool
from tools.vision_tool import VisionTool

from workflows.protocol import SupportsMemory
from workflows.thresholds import information_quality, select_action


@dataclass(slots=True)
class PokerHandWorkflow:
    """Orchestrates a single hand decision from read → act → persist.

    Attributes:
        vision: Screen capture + YOLO inference.
        equity: Monte-Carlo equity estimator.
        action: Mouse / keyboard action executor.
        memory: Key-value store shared across agents.
        rng:    Showdown-based RNG auditor.
    """

    vision: VisionTool
    equity: EquityTool
    action: ActionTool
    memory: SupportsMemory
    rng: RngTool

    # ── Configuration helpers (env-var readers) ─────────────────────

    @staticmethod
    def _table_profile() -> str:
        """Read ``TITAN_TABLE_PROFILE`` (default ``normal``)."""
        profile = os.getenv("TITAN_TABLE_PROFILE", "normal").strip().lower()
        if profile in {"tight", "aggressive"}:
            return profile
        return "normal"

    @staticmethod
    def _table_position() -> str:
        """Read ``TITAN_TABLE_POSITION`` (default ``mp``)."""
        position = os.getenv("TITAN_TABLE_POSITION", "mp").strip().lower()
        valid_positions = {"utg", "mp", "co", "btn", "sb", "bb"}
        if position in valid_positions:
            return position
        return "mp"

    @staticmethod
    def _opponents_count() -> int:
        """Read ``TITAN_OPPONENTS`` (default ``1``, range ``[1, 9]``)."""
        raw_value = os.getenv("TITAN_OPPONENTS", "1").strip()
        if not raw_value.isdigit():
            return 1
        return min(max(int(raw_value), 1), 9)

    @staticmethod
    def _simulations_count() -> int:
        """Read ``TITAN_SIMULATIONS`` (default ``10 000``, range ``[100, 100 000]``)."""
        raw_value = os.getenv("TITAN_SIMULATIONS", "10000").strip()
        if not raw_value.isdigit():
            return 10_000
        return min(max(int(raw_value), 100), 100_000)

    @staticmethod
    def _dynamic_simulations_enabled() -> bool:
        """Read ``TITAN_DYNAMIC_SIMULATIONS`` (default off)."""
        raw_value = os.getenv("TITAN_DYNAMIC_SIMULATIONS", "0").strip().lower()
        return raw_value in {"1", "true", "yes", "on"}

    @staticmethod
    def _rng_evasion_enabled() -> bool:
        """Read ``TITAN_RNG_EVASION`` (default on)."""
        raw_value = os.getenv("TITAN_RNG_EVASION", "1").strip().lower()
        return raw_value in {"1", "true", "yes", "on"}

    @staticmethod
    def _current_opponent(memory: SupportsMemory) -> str:
        """Resolve the current opponent from memory, then env-var fallback."""
        memory_value = memory.get("current_opponent", "")
        if isinstance(memory_value, str) and memory_value.strip():
            return memory_value.strip()
        return os.getenv("TITAN_CURRENT_OPPONENT", "").strip()

    @staticmethod
    def _heads_up_obfuscation(memory: SupportsMemory) -> bool:
        """Return ``True`` when HiveBrain flagged a heads-up collusion scenario.

        In this case, the workflow must play aggressively (never check-down)
        so observers see genuine combat between two friendly bots.
        """
        value = memory.get("heads_up_obfuscation", False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _extract_showdown_events(memory: SupportsMemory) -> list[dict[str, Any]]:
        """Pull any pending showdown events from memory and normalise."""
        events = memory.get("showdown_events", [])
        if not isinstance(events, list):
            return []
        return [event for event in events if isinstance(event, dict)]

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        """Coerce *value* to float, returning *default* on failure."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        return default

    # ── Simulation scaling ──────────────────────────────────────────

    @staticmethod
    def _effective_simulations(
        base_simulations: int,
        street: str,
        opponents_count: int,
        dynamic_enabled: bool,
    ) -> int:
        """Scale the simulation count based on street depth and opponent count.

        * Preflop uses fewer simulations (less uncertainty).
        * River uses more (every card matters).
        * Multiway hands get an extra boost (up to +40 %).
        """
        if not dynamic_enabled:
            return base_simulations

        street_multiplier: dict[str, float] = {
            "preflop": 0.40,
            "flop":    0.70,
            "turn":    1.00,
            "river":   1.25,
        }
        multiplier = street_multiplier.get(street, 1.0)
        multiway_boost = 1.0 + min(max(opponents_count - 1, 0) * 0.08, 0.40)
        effective = int(base_simulations * multiplier * multiway_boost)
        return min(max(effective, 100), 100_000)

    # ── Card utilities ──────────────────────────────────────────────

    @staticmethod
    def _normalize_card(card: str) -> str | None:
        """Normalise a card string to canonical ``Xs`` format."""
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
        """Merge and deduplicate dead cards from multiple sources."""
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
        """Infer the current street from the number of community cards."""
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
        """Compute simple pot odds as ``pot / (pot + stack)``."""
        if pot <= 0 or stack <= 0:
            return 0.0
        return pot / max(pot + stack, 1e-6)

    @staticmethod
    def _spr(pot: float, stack: float) -> float:
        """Compute the stack-to-pot ratio."""
        if pot <= 0 or stack <= 0:
            return 99.0
        return stack / max(pot, 1e-6)

    # ── Main execution pipeline ─────────────────────────────────────

    def execute(self, snapshot: Any | None = None) -> str:
        """Run the full decision pipeline for a single hand.

        Steps:
            1. Read table snapshot (or use the one provided).
            2. Ingest showdown events into the RNG auditor.
            3. Merge dead cards from memory and vision.
            4. Compute equity and information quality.
            5. Select an action via threshold engine.
            6. Apply collusion obfuscation if flagged.
            7. Execute the action on screen.
            8. Persist the decision to memory.

        Args:
            snapshot: Pre-captured :class:`TableSnapshot`, or ``None`` to
                      read from the vision tool.

        Returns:
            Human-readable result string, e.g.
            ``"win_rate=0.65 action=call"``.
        """
        # ── 1. Snapshot ─────────────────────────────────────────────
        snapshot = snapshot if snapshot is not None else self.vision.read_table()

        # ── 2. Showdown ingestion ───────────────────────────────────
        snapshot_events = getattr(snapshot, "showdown_events", [])
        if not isinstance(snapshot_events, list):
            snapshot_events = []

        memory_events = self._extract_showdown_events(self.memory)
        rng_events = [e for e in snapshot_events if isinstance(e, dict)] + memory_events
        for event in rng_events:
            self.rng.ingest_showdown(event)
        if memory_events:
            self.memory.set("showdown_events", [])

        # Persist current opponent from vision
        snapshot_opponent = getattr(snapshot, "current_opponent", "")
        if isinstance(snapshot_opponent, str) and snapshot_opponent.strip():
            self.memory.set("current_opponent", snapshot_opponent.strip())

        # Publish flagged opponents
        flagged_opponents = self.rng.flagged_opponents()
        self.memory.set("rng_super_users", flagged_opponents)

        # ── 3. Dead-card merge ──────────────────────────────────────
        memory_dead_cards = self.memory.get("dead_cards", [])
        if not isinstance(memory_dead_cards, list):
            memory_dead_cards = []
        snapshot_dead_cards = getattr(snapshot, "dead_cards", [])
        if not isinstance(snapshot_dead_cards, list):
            snapshot_dead_cards = []

        dead_cards = self._merge_dead_cards(memory_dead_cards, snapshot_dead_cards)
        visible_cards = {
            *(self._normalize_card(c) for c in snapshot.hero_cards),
            *(self._normalize_card(c) for c in snapshot.board_cards),
        }
        dead_cards = [c for c in dead_cards if c not in visible_cards]
        self.memory.set("dead_cards", dead_cards)

        # ── 4. Equity computation ───────────────────────────────────
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
        info_quality = information_quality(
            snapshot.hero_cards, snapshot.board_cards, dead_cards,
        )
        score = estimate.win_rate + (estimate.tie_rate * 0.5)
        pot_odds = self._pot_odds(snapshot.pot, snapshot.stack)

        # ── 5. Action selection ─────────────────────────────────────
        if len(snapshot.hero_cards) < 2:
            decision = "wait"
            score = 0.0
            pot_odds = self._pot_odds(snapshot.pot, snapshot.stack)
        else:
            decision, score, pot_odds = select_action(
                win_rate=estimate.win_rate,
                tie_rate=estimate.tie_rate,
                street=street,
                pot=snapshot.pot,
                stack=snapshot.stack,
                info_quality=info_quality,
                table_profile=table_profile,
                table_position=table_position,
                opponents_count=opponents_count,
            )

        # ── 6. RNG evasion ──────────────────────────────────────────
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

        # ── 7. Collusion obfuscation ────────────────────────────────
        # When two friendly bots are heads-up, never check-down —
        # escalate passive actions to look aggressive.
        hu_obfuscation = self._heads_up_obfuscation(self.memory)
        if hu_obfuscation and decision not in {"wait", "fold"}:
            if decision == "call":
                decision = "raise_small"
            elif decision == "raise_small" and score >= 0.55:
                decision = "raise_big"

        # ── 8. Execute + persist ────────────────────────────────────
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

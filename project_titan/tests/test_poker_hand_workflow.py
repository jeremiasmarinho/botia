from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from workflows.poker_hand_workflow import Decision, PokerHandWorkflow


pytest.importorskip("treys")


@dataclass
class Snapshot:
    hero_cards: list[str] = field(default_factory=lambda: ["As", "Ah", "Kd", "Qc", "Jd", "Ts"])
    board_cards: list[str] = field(default_factory=lambda: ["2c", "7d", "9h"])
    dead_cards: list[str] = field(default_factory=list)
    showdown_events: list[dict[str, Any]] = field(default_factory=list)
    current_opponent: str = "villain_01"
    pot: float = 100.0
    stack: float = 100.0


class DummyVision:
    def read_table(self) -> Snapshot:
        return Snapshot()


class DummyEquityEstimate:
    def __init__(self, win_rate: float, tie_rate: float) -> None:
        self.win_rate = win_rate
        self.tie_rate = tie_rate


class DummyEquity:
    def __init__(self, win_rate: float = 0.50, tie_rate: float = 0.0) -> None:
        self._win_rate = win_rate
        self._tie_rate = tie_rate

    def estimate(self, *args: Any, **kwargs: Any) -> DummyEquityEstimate:
        return DummyEquityEstimate(self._win_rate, self._tie_rate)


class DummyAction:
    def act(self, action: str, street: str = "preflop") -> str:
        return f"action={action} street={street}"


class DummyMemory:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def set(self, key: str, value: Any, **kwargs: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class DummyAlert:
    def __init__(self, should_evade: bool = False) -> None:
        self.should_evade = should_evade
        self.z_score = 0.0
        self.sample_count = 0


class DummyRng:
    def ingest_showdown(self, event: dict[str, Any]) -> None:
        return None

    def flagged_opponents(self) -> list[str]:
        return []

    def should_evade(self, opponent: str) -> DummyAlert:
        return DummyAlert(False)


def _build_workflow(win_rate: float = 0.50) -> PokerHandWorkflow:
    return cast(
        PokerHandWorkflow,
        PokerHandWorkflow(
            vision=cast(Any, DummyVision()),
            equity=cast(Any, DummyEquity(win_rate=win_rate)),
            action=cast(Any, DummyAction()),
            memory=cast(Any, DummyMemory()),
            rng=cast(Any, DummyRng()),
        ),
    )


def test_execute_returns_decision_object() -> None:
    workflow = _build_workflow(win_rate=0.52)
    decision = workflow.execute(snapshot=Snapshot(), hive_data={"mode": "solo", "dead_cards": []})

    assert isinstance(decision, Decision)
    assert decision.action in {"fold", "call", "raise_small", "raise_big", "all_in", "wait"}


def test_commitment_rule_forces_all_in() -> None:
    workflow = _build_workflow(win_rate=0.60)
    snapshot = Snapshot(pot=100.0, stack=100.0)  # SPR = 1.0

    decision = workflow.execute(snapshot=snapshot, hive_data={"mode": "solo", "dead_cards": []})

    assert decision.action == "all_in"
    assert decision.committed is True


def test_god_mode_sets_mode_label() -> None:
    workflow = _build_workflow(win_rate=0.45)
    snapshot = Snapshot(pot=200.0, stack=500.0)

    decision = workflow.execute(
        snapshot=snapshot,
        hive_data={
            "mode": "squad",
            "dead_cards": ["Ac", "Kd"],
            "partners": ["A2"],
            "heads_up_obfuscation": False,
        },
    )

    assert decision.mode == "SQUAD_GOD_MODE"
    assert "God Mode" in decision.description


def test_hive_data_dead_cards_are_persisted() -> None:
    workflow = _build_workflow(win_rate=0.40)
    snapshot = Snapshot()

    decision = workflow.execute(
        snapshot=snapshot,
        hive_data={"mode": "squad", "dead_cards": ["Ac", "Kd"], "partners": ["A2"]},
    )

    assert isinstance(decision, Decision)
    saved = workflow.memory.get("last_decision", {})
    assert "dead_cards" in saved
    assert "Ac" in saved["dead_cards"] or "Kd" in saved["dead_cards"]


def test_execute_handles_hive_none() -> None:
    workflow = _build_workflow(win_rate=0.35)
    decision = workflow.execute(snapshot=Snapshot(), hive_data=None)

    assert isinstance(decision, Decision)
    assert decision.mode == "SOLO"

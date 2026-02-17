from __future__ import annotations

import pytest

from core.math_engine import MathEngine


pytest.importorskip("treys")


def test_estimate_equity_returns_bounded_rates() -> None:
    engine = MathEngine()

    result = engine.estimate_equity(
        hero_cards=["As", "Ah", "Kd", "Qc", "Jd", "Ts"],
        board_cards=["2c", "7d", "9h"],
        dead_cards=["Ac", "Kh"],
        simulations=150,
        opponents=1,
    )

    assert result.simulations > 0
    assert 0.0 <= result.win_rate <= 1.0
    assert 0.0 <= result.tie_rate <= 1.0
    assert (result.win_rate + result.tie_rate) <= 1.0


def test_estimate_equity_returns_zero_for_invalid_hero() -> None:
    engine = MathEngine()

    result = engine.estimate_equity(
        hero_cards=["As"],
        board_cards=[],
        dead_cards=[],
        simulations=50,
        opponents=1,
    )

    assert result.simulations == 0
    assert result.win_rate == 0.0
    assert result.tie_rate == 0.0

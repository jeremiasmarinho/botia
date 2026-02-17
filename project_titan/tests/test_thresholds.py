from __future__ import annotations

from workflows.thresholds import information_quality, select_action


def test_information_quality_increases_with_observed_cards() -> None:
    low = information_quality(hero_cards=["As", "Kd"], board_cards=[], dead_cards=[])
    high = information_quality(
        hero_cards=["As", "Kd", "Qc", "Jh", "Ts", "9d"],
        board_cards=["2c", "7d", "9h", "Tc", "Jc"],
        dead_cards=["Ac"],
    )

    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert high >= low


def test_select_action_high_equity_is_aggressive() -> None:
    action, score, _ = select_action(
        win_rate=0.92,
        tie_rate=0.03,
        street="turn",
        pot=100.0,
        stack=200.0,
        info_quality=0.95,
        table_profile="normal",
        table_position="btn",
        opponents_count=1,
    )

    assert score > 0.9
    assert action in {"raise_small", "raise_big"}


def test_select_action_low_equity_folds() -> None:
    action, score, _ = select_action(
        win_rate=0.10,
        tie_rate=0.01,
        street="river",
        pot=120.0,
        stack=80.0,
        info_quality=0.9,
        table_profile="tight",
        table_position="utg",
        opponents_count=3,
    )

    assert score < 0.2
    assert action == "fold"

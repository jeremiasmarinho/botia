"""Tests for workflows.thresholds — pot odds direction and edge cases."""

from __future__ import annotations

from workflows.thresholds import select_action, information_quality


class TestPotOddsDirection:
    """When hero is getting good pot odds, thresholds should be LOWER (more aggressive)."""

    def test_good_pot_odds_lowers_call_threshold(self) -> None:
        """With a big pot (good odds), hero should call with weaker hands."""
        base_args = dict(
            win_rate=0.42,
            tie_rate=0.0,
            street="flop",
            info_quality=1.0,
            table_profile="normal",
            table_position="mp",
            opponents_count=1,
        )

        # Small pot (bad odds) → tighter
        action_small, _, _ = select_action(stack=100.0, pot=10.0, **base_args)
        # Big pot (good odds) → should be looser
        action_big, _, _ = select_action(stack=100.0, pot=500.0, **base_args)

        # With a huge pot, 42% equity should be a call (not fold)
        # With a tiny pot, 42% equity might still fold
        assert action_big in ("call", "raise_small", "raise_big"), (
            f"Expected call/raise with good pot odds, got {action_big}"
        )

    def test_pot_odds_adjustment_monotonic(self) -> None:
        """Increasing pot size should not make the agent more conservative."""
        _, score, _ = select_action(
            win_rate=0.45, tie_rate=0.0, street="flop",
            pot=10.0, stack=100.0, info_quality=1.0,
            table_profile="normal", table_position="mp", opponents_count=1,
        )
        action_small, _, pot_odds_small = select_action(
            win_rate=0.45, tie_rate=0.0, street="flop",
            pot=10.0, stack=100.0, info_quality=1.0,
            table_profile="normal", table_position="mp", opponents_count=1,
        )
        action_big, _, pot_odds_big = select_action(
            win_rate=0.45, tie_rate=0.0, street="flop",
            pot=500.0, stack=100.0, info_quality=1.0,
            table_profile="normal", table_position="mp", opponents_count=1,
        )
        action_rank = {"fold": 0, "call": 1, "raise_small": 2, "raise_big": 3}
        # Bigger pot should lead to equal or more aggressive action
        assert action_rank[action_big] >= action_rank[action_small], (
            f"Big pot got {action_big} but small pot got {action_small}"
        )


class TestSelectActionEdgeCases:
    def test_zero_equity_folds(self) -> None:
        action, _, _ = select_action(
            win_rate=0.0, tie_rate=0.0, street="flop",
            pot=100.0, stack=100.0, info_quality=1.0,
            table_profile="normal", table_position="mp", opponents_count=1,
        )
        assert action == "fold"

    def test_high_equity_raises(self) -> None:
        action, _, _ = select_action(
            win_rate=0.95, tie_rate=0.0, street="flop",
            pot=100.0, stack=100.0, info_quality=1.0,
            table_profile="normal", table_position="mp", opponents_count=1,
        )
        assert action in ("raise_small", "raise_big")

    def test_zero_pot_and_stack(self) -> None:
        """Should not crash with zero values."""
        action, _, _ = select_action(
            win_rate=0.50, tie_rate=0.0, street="preflop",
            pot=0.0, stack=0.0, info_quality=0.5,
            table_profile="normal", table_position="mp", opponents_count=1,
        )
        assert action in ("fold", "call", "raise_small", "raise_big")


class TestInformationQuality:
    def test_full_information(self) -> None:
        assert information_quality(
            hero_cards=["Ah", "Kh", "Qh", "Jh", "Th", "9h"],
            board_cards=["2c", "3c", "4c", "5c", "6c"],
            dead_cards=["7d"],
        ) == 1.0

    def test_no_information(self) -> None:
        assert information_quality([], [], []) == 0.0

    def test_partial_clamped(self) -> None:
        result = information_quality(
            hero_cards=["Ah", "Kh"],
            board_cards=[],
            dead_cards=[],
        )
        assert 0.0 <= result <= 1.0

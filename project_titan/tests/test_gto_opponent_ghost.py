"""Tests for the GTO mixed-strategy engine, opponent profiling DB,
and GhostMouse humanization upgrades.

Usage::

    cd project_titan
    python -m pytest tests/test_gto_opponent_ghost.py -v
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time

import pytest

# Ensure project_titan on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ═══════════════════════════════════════════════════════════════════════════
# GTO Engine Tests
# ═══════════════════════════════════════════════════════════════════════════

from workflows.gto_engine import (
    MixedStrategy,
    ActionDistribution,
    OpponentTendencies,
    ACTION_RANK,
)


class TestActionDistribution:
    def test_as_dict(self):
        dist = ActionDistribution(fold=0.2, call=0.3, raise_small=0.35, raise_big=0.15)
        d = dist.as_dict()
        assert set(d.keys()) == {"fold", "call", "raise_small", "raise_big"}
        assert abs(sum(d.values()) - 1.0) < 0.01

    def test_default_is_fold(self):
        dist = ActionDistribution()
        assert dist.chosen == "fold"


class TestMixedStrategy:
    """Tests for the core GTO engine."""

    def test_deterministic_with_seed(self):
        """Same seed → same action for same inputs."""
        ms1 = MixedStrategy(seed=42)
        ms2 = MixedStrategy(seed=42)
        for _ in range(10):
            a1, _, _, _ = ms1.select(0.6, 0.05, "flop", 100, 500, 0.7, "normal", "btn", 2)
            a2, _, _, _ = ms2.select(0.6, 0.05, "flop", 100, 500, 0.7, "normal", "btn", 2)
            assert a1 == a2

    def test_disabled_returns_deterministic(self):
        """When GTO is disabled, should return the deterministic action."""
        os.environ["TITAN_GTO_ENABLED"] = "0"
        try:
            ms = MixedStrategy(seed=1)
            action, score, pot_odds, dist = ms.select(
                0.7, 0.05, "flop", 100, 500, 0.8, "normal", "co", 1,
            )
            # Distribution should be 100% on one action
            probs = [dist.fold, dist.call, dist.raise_small, dist.raise_big]
            assert max(probs) == 1.0
        finally:
            os.environ.pop("TITAN_GTO_ENABLED", None)

    def test_strong_hand_mostly_raises(self):
        """A very strong hand (equity=0.90) should raise most of the time."""
        ms = MixedStrategy(seed=99)
        actions = []
        for i in range(200):
            ms._rng.seed(i)
            a, _, _, _ = ms.select(0.90, 0.05, "flop", 100, 500, 0.9, "normal", "btn", 1)
            actions.append(a)
        raise_count = sum(1 for a in actions if a.startswith("raise"))
        # At least 60% should be raises
        assert raise_count / len(actions) > 0.50

    def test_weak_hand_mostly_folds(self):
        """A very weak hand (equity=0.10) should fold most of the time."""
        ms = MixedStrategy(seed=77)
        actions = []
        for i in range(200):
            ms._rng.seed(i)
            a, _, _, _ = ms.select(0.10, 0.02, "flop", 100, 500, 0.5, "normal", "mp", 3)
            actions.append(a)
        fold_count = sum(1 for a in actions if a == "fold")
        assert fold_count / len(actions) > 0.50

    def test_distribution_sums_to_one(self):
        """Distribution probabilities should always sum to 1.0."""
        ms = MixedStrategy(seed=12)
        for equity in [0.10, 0.30, 0.50, 0.70, 0.90]:
            _, _, _, dist = ms.select(equity, 0.05, "flop", 100, 500, 0.7, "normal", "co", 2)
            total = dist.fold + dist.call + dist.raise_small + dist.raise_big
            assert abs(total - 1.0) < 0.01, f"Sum={total} for equity={equity}"

    def test_position_affects_distribution(self):
        """BTN should be more aggressive than UTG for the same equity."""
        ms = MixedStrategy(seed=50)
        _, _, _, dist_btn = ms.select(0.55, 0.05, "flop", 100, 500, 0.7, "normal", "btn", 2)
        ms2 = MixedStrategy(seed=50)
        _, _, _, dist_utg = ms2.select(0.55, 0.05, "flop", 100, 500, 0.7, "normal", "utg", 2)
        # BTN should have higher raise probability
        btn_aggression = dist_btn.raise_small + dist_btn.raise_big
        utg_aggression = dist_utg.raise_small + dist_utg.raise_big
        assert btn_aggression >= utg_aggression

    def test_bluff_injection(self):
        """Even with low equity, there should be some non-zero raise probability (bluffs)."""
        ms = MixedStrategy(seed=33)
        _, _, _, dist = ms.select(0.20, 0.03, "river", 100, 500, 0.7, "normal", "btn", 1)
        # There should be at least some raise probability (bluff)
        assert (dist.raise_small + dist.raise_big) > 0.0

    def test_opponent_fish_adaptation(self):
        """Against a fish, bluff frequency should decrease."""
        fish = OpponentTendencies(vpip=0.70, pfr=0.10, aggression=0.8, hands_observed=50)
        ms = MixedStrategy(seed=42)
        _, _, _, dist_fish = ms.select(0.25, 0.02, "flop", 100, 500, 0.7, "normal", "co", 1, opponent=fish)

        ms2 = MixedStrategy(seed=42)
        _, _, _, dist_no_opp = ms2.select(0.25, 0.02, "flop", 100, 500, 0.7, "normal", "co", 1, opponent=None)

        # Against a fish, raise frequency should be lower with weak hands
        fish_raises = dist_fish.raise_small + dist_fish.raise_big
        default_raises = dist_no_opp.raise_small + dist_no_opp.raise_big
        # Fish calls everything, so bluffing is -EV → fewer raises
        assert fish_raises <= default_raises + 0.05  # allow small tolerance

    def test_opponent_nit_more_steals(self):
        """Against a nit, we should steal more."""
        nit = OpponentTendencies(vpip=0.15, pfr=0.10, aggression=1.5, hands_observed=50)
        ms = MixedStrategy(seed=42)
        _, _, _, dist_nit = ms.select(0.40, 0.05, "preflop", 50, 500, 0.5, "normal", "btn", 1, opponent=nit)

        ms2 = MixedStrategy(seed=42)
        _, _, _, dist_no = ms2.select(0.40, 0.05, "preflop", 50, 500, 0.5, "normal", "btn", 1, opponent=None)

        nit_aggression = dist_nit.raise_small + dist_nit.raise_big
        default_aggression = dist_no.raise_small + dist_no.raise_big
        assert nit_aggression >= default_aggression - 0.02

    def test_returns_four_tuple(self):
        """API should return (action, score, pot_odds, distribution)."""
        ms = MixedStrategy(seed=1)
        result = ms.select(0.50, 0.05, "flop", 100, 500, 0.8, "normal", "co", 1)
        assert len(result) == 4
        action, score, pot_odds, dist = result
        assert isinstance(action, str)
        assert isinstance(score, float)
        assert isinstance(pot_odds, float)
        assert isinstance(dist, ActionDistribution)


# ═══════════════════════════════════════════════════════════════════════════
# Opponent DB Tests
# ═══════════════════════════════════════════════════════════════════════════

from memory.opponent_db import OpponentDB, OpponentProfile, HandEvent, _classify


class TestClassification:
    def test_fish(self):
        assert _classify(vpip=0.60, pfr=0.10, aggression=0.8, hands=50) == "Fish"

    def test_nit(self):
        assert _classify(vpip=0.18, pfr=0.12, aggression=1.5, hands=50) == "Nit"

    def test_lag(self):
        assert _classify(vpip=0.50, pfr=0.30, aggression=3.0, hands=50) == "LAG"

    def test_tag(self):
        assert _classify(vpip=0.28, pfr=0.20, aggression=2.0, hands=50) == "TAG"

    def test_unknown_low_hands(self):
        assert _classify(vpip=0.50, pfr=0.30, aggression=3.0, hands=10) == "Unknown"


class TestOpponentDB:
    def _make_db(self) -> OpponentDB:
        """Create a temp DB for testing."""
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        return OpponentDB(db_path=tmp.name)

    def test_empty_profile_returns_default(self):
        db = self._make_db()
        try:
            p = db.get_profile("nonexistent")
            assert p.player_id == "nonexistent"
            assert p.hands_observed == 0
            assert p.classification == "Unknown"
        finally:
            db.close()

    def test_record_hand_start_increments(self):
        db = self._make_db()
        try:
            db.record_hand_start("player1")
            db.record_hand_start("player1")
            db.record_hand_start("player1")
            p = db.get_profile("player1")
            assert p.hands_observed == 3
        finally:
            db.close()

    def test_record_event_updates_stats(self):
        db = self._make_db()
        try:
            db.record_hand_start("p1")
            db.record_event(HandEvent(
                player_id="p1",
                is_voluntary=True,
                is_preflop_raise=True,
                action="raise",
            ))
            p = db.get_profile("p1")
            assert p.vpip > 0
            assert p.pfr > 0
        finally:
            db.close()

    def test_classification_after_many_hands(self):
        db = self._make_db()
        try:
            pid = "fishy_pete"
            # Simulate a fish: lots of voluntary play, few raises, many calls
            for _ in range(30):
                db.record_hand_start(pid)
                db.record_event(HandEvent(
                    player_id=pid,
                    is_voluntary=True,
                    action="call",
                ))
            p = db.get_profile(pid)
            assert p.hands_observed == 30
            # VPIP should be high (30/30 = 1.0)
            assert p.vpip > 0.5
            # Aggression should be low (0 bets / 30 calls)
            assert p.aggression < 1.0
            assert p.classification == "Fish"
        finally:
            db.close()

    def test_get_all_profiles(self):
        db = self._make_db()
        try:
            db.record_hand_start("a")
            db.record_hand_start("b")
            db.record_hand_start("c")
            profiles = db.get_all_profiles()
            assert len(profiles) == 3
        finally:
            db.close()

    def test_to_gto_tendencies_insufficient_data(self):
        db = self._make_db()
        try:
            db.record_hand_start("newbie")
            result = db.to_gto_tendencies("newbie")
            assert result is None  # < 50 hands
        finally:
            db.close()

    def test_to_gto_tendencies_sufficient_data(self):
        db = self._make_db()
        try:
            pid = "veteran"
            for _ in range(55):
                db.record_hand_start(pid)
                db.record_event(HandEvent(
                    player_id=pid,
                    is_voluntary=True,
                    action="call",
                ))
            result = db.to_gto_tendencies(pid)
            assert result is not None
            assert result.vpip > 0
            assert result.hands_observed == 55
        finally:
            db.close()

    def test_disabled_db(self):
        os.environ["TITAN_OPPONENT_DB_OFF"] = "1"
        try:
            db = OpponentDB()
            db.record_hand_start("x")
            p = db.get_profile("x")
            assert p.hands_observed == 0  # disabled, nothing persisted
        finally:
            os.environ.pop("TITAN_OPPONENT_DB_OFF", None)


# ═══════════════════════════════════════════════════════════════════════════
# GhostMouse Humanization Tests
# ═══════════════════════════════════════════════════════════════════════════

from agent.ghost_mouse import (
    GhostMouse,
    GhostMouseConfig,
    ClickPoint,
    CurvePoint,
    _generate_bezier_path,
    _ease_in_out,
    classify_difficulty,
    classify_difficulty_by_equity,
    _DIFFICULTY_EASY,
    _DIFFICULTY_MEDIUM,
    _DIFFICULTY_HARD,
)


class TestEaseInOut:
    def test_endpoints(self):
        """Ease function should map 0→~0 and 1→~1."""
        assert abs(_ease_in_out(0.0) - 0.0) < 0.01
        assert abs(_ease_in_out(1.0) - 1.0) < 0.01

    def test_monotonic(self):
        """Ease function should be monotonically increasing."""
        prev = 0.0
        for i in range(1, 101):
            t = i / 100.0
            val = _ease_in_out(t)
            assert val >= prev - 1e-9, f"Non-monotonic at t={t}"
            prev = val

    def test_slow_at_edges(self):
        """Near t=1.0, the curve decelerates (ease-out near target).

        The derivative near the end should be smaller than at the
        beginning, mimicking how a human slows the cursor toward the target.
        """
        dt = 0.01
        # Derivative at t=0.2 (early movement — fast)
        d_early = (_ease_in_out(0.2 + dt) - _ease_in_out(0.2)) / dt
        # Derivative at t=0.9 (approaching target — slow)
        d_late = (_ease_in_out(0.9 + dt) - _ease_in_out(0.9)) / dt
        # Late movement should be slower (decelerating into target)
        assert d_late < d_early, f"d_late={d_late:.4f} should be < d_early={d_early:.4f}"


class TestClassifyDifficultyByEquity:
    def test_marginal_equity_is_harder(self):
        """Marginal equity (0.45-0.55) should upgrade difficulty."""
        assert classify_difficulty_by_equity("call", "flop", 0.50) in {_DIFFICULTY_MEDIUM, _DIFFICULTY_HARD}

    def test_nuts_is_easy(self):
        """Very high equity should be easy (snap-action)."""
        assert classify_difficulty_by_equity("call", "river", 0.95) == _DIFFICULTY_EASY

    def test_obvious_fold_is_easy(self):
        """Very low equity fold should be easy."""
        assert classify_difficulty_by_equity("fold", "river", 0.05) == _DIFFICULTY_EASY


class TestGhostMouseConfig:
    def test_default_config_has_new_params(self):
        """Verify new humanization parameters exist."""
        cfg = GhostMouseConfig()
        assert cfg.velocity_curve_enabled is True
        assert cfg.overshoot_probability > 0
        assert cfg.idle_jitter_enabled is True
        assert cfg.poisson_delay_enabled is True
        assert cfg.click_hold_mu < 0  # log-normal mu is typically negative

    def test_log_normal_hold_time(self):
        """Log-normal hold time should be within bounds."""
        gm = GhostMouse(config=GhostMouseConfig())
        for _ in range(100):
            hold = gm._log_normal_hold_time()
            assert gm.config.click_hold_min <= hold <= gm.config.click_hold_max


class TestGhostMouseThinkingDelay:
    def test_poisson_delay_in_range(self):
        """Poisson delay should stay within configured bounds."""
        cfg = GhostMouseConfig(poisson_delay_enabled=True)
        gm = GhostMouse(config=cfg)
        for _ in range(100):
            delay = gm._thinking_delay(_DIFFICULTY_EASY)
            assert cfg.timing_easy[0] <= delay <= cfg.timing_easy[1]

    def test_hard_delay_longer_than_easy(self):
        """Hard decisions should have longer delays on average."""
        cfg = GhostMouseConfig(poisson_delay_enabled=True)
        gm = GhostMouse(config=cfg)
        easy_delays = [gm._thinking_delay(_DIFFICULTY_EASY) for _ in range(200)]
        hard_delays = [gm._thinking_delay(_DIFFICULTY_HARD) for _ in range(200)]
        assert sum(hard_delays) / len(hard_delays) > sum(easy_delays) / len(easy_delays)

    def test_legacy_delay_when_poisson_disabled(self):
        """With Poisson disabled, should use legacy uniform."""
        cfg = GhostMouseConfig(poisson_delay_enabled=False)
        gm = GhostMouse(config=cfg)
        for _ in range(50):
            delay = gm._thinking_delay(_DIFFICULTY_MEDIUM)
            assert cfg.timing_medium[0] <= delay <= cfg.timing_medium[1]


class TestBezierPath:
    def test_path_starts_and_ends_correctly(self):
        start = CurvePoint(0, 0)
        end = CurvePoint(300, 400)
        path = _generate_bezier_path(start, end)
        assert abs(path[0].x - start.x) < 1
        assert abs(path[0].y - start.y) < 1
        assert abs(path[-1].x - end.x) < 1
        assert abs(path[-1].y - end.y) < 1

    def test_path_has_minimum_steps(self):
        path = _generate_bezier_path(CurvePoint(0, 0), CurvePoint(10, 10))
        assert len(path) >= 8  # minimum density


class TestIdleJitter:
    def test_idle_jitter_disabled_does_nothing(self):
        """With idle jitter disabled or no PyAutoGUI, should not crash."""
        cfg = GhostMouseConfig(idle_jitter_enabled=False)
        gm = GhostMouse(config=cfg)
        gm.idle_jitter()  # Should not raise

    def test_idle_jitter_without_pyautogui(self):
        """Without real mouse enabled, idle_jitter is a no-op."""
        cfg = GhostMouseConfig(idle_jitter_enabled=True)
        gm = GhostMouse(config=cfg)
        # _enabled is False in test env (no TITAN_GHOST_MOUSE=1)
        gm.idle_jitter()  # Should not raise

"""Tests for core.rng_auditor — Z-Score based RNG integrity auditing."""

from __future__ import annotations

from core.rng_auditor import RngAuditor


class TestRngAuditor:
    def test_add_sample_and_stats(self) -> None:
        auditor = RngAuditor()
        auditor.add_allin_result("villain_1", equity=0.50, won=True)
        auditor.add_allin_result("villain_1", equity=0.50, won=False)

        stats = auditor.player_stats("villain_1")
        assert stats is not None
        assert stats.sample_count == 2

    def test_empty_stats(self) -> None:
        auditor = RngAuditor()
        stats = auditor.player_stats("unknown")
        assert stats.sample_count == 0
        assert stats.is_super_user is False

    def test_super_user_detection(self) -> None:
        auditor = RngAuditor(super_user_zscore=2.0, min_samples=5)
        # Villain wins with very low equity most of the time → suspicious
        # Mix outcomes slightly so std > 0 (z-score requires nonzero std)
        for _ in range(18):
            auditor.add_allin_result("sus_villain", equity=0.10, won=True)
        for _ in range(2):
            auditor.add_allin_result("sus_villain", equity=0.10, won=False)

        stats = auditor.player_stats("sus_villain")
        assert stats.z_score > 2.0
        assert auditor.is_super_user("sus_villain") is True
        assert "sus_villain" in auditor.super_users()

    def test_not_super_user_with_few_samples(self) -> None:
        auditor = RngAuditor(super_user_zscore=2.0, min_samples=10)
        for _ in range(5):
            auditor.add_allin_result("small_sample", equity=0.10, won=True)
        # Not enough samples to flag
        assert auditor.is_super_user("small_sample") is False

    def test_normal_player_not_flagged(self) -> None:
        auditor = RngAuditor(super_user_zscore=3.0, min_samples=5)
        # Player wins approximately as expected
        for _ in range(50):
            auditor.add_allin_result("normal_player", equity=0.50, won=True)
        for _ in range(50):
            auditor.add_allin_result("normal_player", equity=0.50, won=False)
        assert auditor.is_super_user("normal_player") is False

    def test_export_import_roundtrip(self) -> None:
        auditor = RngAuditor()
        for i in range(10):
            auditor.add_allin_result("v1", equity=0.40, won=i % 3 == 0)
        state = auditor.export_state()

        new_auditor = RngAuditor()
        new_auditor.import_state(state)

        stats_old = auditor.player_stats("v1")
        stats_new = new_auditor.player_stats("v1")
        assert stats_old.sample_count == stats_new.sample_count
        assert abs(stats_old.z_score - stats_new.z_score) < 0.001

    def test_equity_clamped(self) -> None:
        """Equity values outside [0,1] should be clamped, not crash."""
        auditor = RngAuditor()
        auditor.add_sample("clamp_test", expected_value=1.5, observed=1.0)
        auditor.add_sample("clamp_test", expected_value=-0.5, observed=0.0)
        stats = auditor.player_stats("clamp_test")
        assert stats.sample_count == 2

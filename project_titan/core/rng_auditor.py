"""Statistical RNG auditor for detecting super-user opponents.

Collects all-in showdown samples ``(expected_equity, actual_outcome)`` and
computes a z-score for each opponent.  If the z-score exceeds a configurable
threshold (default 3.0) with enough samples, the opponent is flagged.

The z-score measures how many standard errors the mean residual
``(observed - expected)`` deviates from zero.  A large positive z-score
means the opponent wins significantly more than their equity predicts.

Maintainer notes
-----------------
* The auditor is **append-only** — samples are never removed in normal
  operation.  Use :meth:`export_state` / :meth:`import_state` to
  serialise across restarts.
* ``max_samples_per_player`` in export truncates the oldest samples to
  bound storage growth.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt


EPSILON = 1e-9


@dataclass(slots=True)
class AuditSample:
    """Single showdown observation.

    Attributes:
        expected_value: Opponent's equity at the time of all-in.
        observed:       ``1.0`` if opponent won, ``0.0`` if lost.
    """

    expected_value: float
    observed: float

    @property
    def residual(self) -> float:
        return self.observed - self.expected_value


@dataclass(slots=True)
class PlayerAuditStats:
    """Aggregated statistics for a single player.

    Attributes:
        player_id:     Normalised opponent identifier.
        sample_count:  Total observed showdowns.
        residual_mean: Mean of ``(observed - expected)``.
        residual_std:  Sample standard deviation of residuals.
        z_score:       ``mean / standard_error``.
        is_super_user: ``True`` when z-score exceeds the threshold.
    """

    player_id: str
    sample_count: int
    residual_mean: float
    residual_std: float
    z_score: float
    is_super_user: bool


class RngAuditor:
    """Accumulates showdown samples and flags statistically anomalous players."""

    def __init__(self, super_user_zscore: float = 3.0, min_samples: int = 25) -> None:
        self._samples: dict[str, list[AuditSample]] = defaultdict(list)
        self.super_user_zscore = float(super_user_zscore)
        self.min_samples = max(3, int(min_samples))

    def add_sample(self, player_id: str, expected_value: float, observed: float) -> None:
        expected = min(max(float(expected_value), 0.0), 1.0)
        obs = min(max(float(observed), 0.0), 1.0)
        self._samples[player_id].append(AuditSample(expected, obs))

    def add_allin_result(self, player_id: str, equity: float, won: bool) -> None:
        self.add_sample(player_id=player_id, expected_value=equity, observed=1.0 if won else 0.0)

    def player_sample_count(self, player_id: str) -> int:
        return len(self._samples[player_id])

    def _residuals(self, player_id: str) -> list[float]:
        samples = self._samples[player_id]
        return [sample.residual for sample in samples]

    @staticmethod
    def _mean(values: list[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    @staticmethod
    def _sample_std(values: list[float], mean: float) -> float:
        if len(values) < 2:
            return 0.0
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        return sqrt(max(variance, 0.0))

    @classmethod
    def _z_score_from_residuals(cls, residuals: list[float]) -> float:
        sample_count = len(residuals)
        if sample_count < 2:
            return 0.0

        mean_value = cls._mean(residuals)
        std_value = cls._sample_std(residuals, mean_value)
        if std_value <= EPSILON:
            return 0.0

        standard_error = std_value / sqrt(sample_count)
        if standard_error <= EPSILON:
            return 0.0

        return mean_value / standard_error

    def player_stats(self, player_id: str) -> PlayerAuditStats:
        residuals = self._residuals(player_id)
        sample_count = len(residuals)
        mean_value = self._mean(residuals)
        std_value = self._sample_std(residuals, mean_value)
        z_score = self._z_score_from_residuals(residuals)

        is_super_user = sample_count >= self.min_samples and z_score >= self.super_user_zscore
        return PlayerAuditStats(
            player_id=player_id,
            sample_count=sample_count,
            residual_mean=mean_value,
            residual_std=std_value,
            z_score=z_score,
            is_super_user=is_super_user,
        )

    def is_super_user(self, player_id: str) -> bool:
        return self.player_stats(player_id).is_super_user

    def all_player_stats(self) -> list[PlayerAuditStats]:
        return [self.player_stats(player_id) for player_id in self._samples]

    def super_users(self) -> list[str]:
        flagged: list[str] = []
        for player_id in self._samples:
            if self.is_super_user(player_id):
                flagged.append(player_id)
        return sorted(flagged)

    def export_state(self, max_samples_per_player: int | None = None) -> dict[str, list[dict[str, float]]]:
        state: dict[str, list[dict[str, float]]] = {}
        max_samples = max_samples_per_player if max_samples_per_player is None else max(1, int(max_samples_per_player))

        for player_id, samples in self._samples.items():
            subset = samples
            if max_samples is not None:
                subset = samples[-max_samples:]

            encoded_samples: list[dict[str, float]] = []
            for sample in subset:
                encoded_samples.append(
                    {
                        "expected_value": float(sample.expected_value),
                        "observed": float(sample.observed),
                    }
                )
            state[player_id] = encoded_samples

        return state

    def import_state(self, state: dict[str, list[dict[str, float]]]) -> None:
        if not isinstance(state, dict):
            return

        for player_id, samples in state.items():
            if not isinstance(player_id, str) or not isinstance(samples, list):
                continue

            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                expected_value = sample.get("expected_value")
                observed = sample.get("observed")
                if not isinstance(expected_value, (int, float)):
                    continue
                if not isinstance(observed, (int, float)):
                    continue
                self.add_sample(player_id=player_id, expected_value=float(expected_value), observed=float(observed))

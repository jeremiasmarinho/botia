"""RNG fairness auditing tool.

Ingests showdown / all-in results and uses a z-score test to flag opponents
whose observed outcomes deviate significantly from their expected equity.

The auditor state is persisted to Redis (or in-memory) so auditing survives
agent restarts.

See :mod:`core.rng_auditor` for the statistical engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from core.rng_auditor import PlayerAuditStats, RngAuditor


@dataclass(slots=True)
class RngAlert:
    """Result of a single-opponent evasion check.

    Attributes:
        opponent_id:   Normalised opponent identifier.
        should_evade:  ``True`` when the z-score exceeds the threshold.
        z_score:       Current z-score for the opponent.
        sample_count:  Number of observed showdowns.
    """

    opponent_id: str
    should_evade: bool
    z_score: float
    sample_count: int


class SupportsStorage:
    """Structural subtype for the persistence backend."""

    def set(self, key: str, value: Any) -> None: ...

    def get(self, key: str, default: Any = None) -> Any: ...


class RngTool:
    """High-level interface for showdown ingestion and evasion checks."""

    def __init__(
        self,
        super_user_zscore: float = 3.0,
        min_samples: int = 25,
        storage: SupportsStorage | None = None,
        storage_key: str | None = None,
        max_samples_per_player: int = 1000,
    ) -> None:
        self.auditor = RngAuditor(super_user_zscore=super_user_zscore, min_samples=min_samples)
        self.storage = storage
        self.storage_key = storage_key or os.getenv("TITAN_RNG_STATE_KEY", "rng_audit_state")
        self.max_samples_per_player = max(50, int(max_samples_per_player))
        self._restore_state()

    @staticmethod
    def _normalize_player_id(player_id: str) -> str:
        return player_id.strip().lower()

    def _restore_state(self) -> None:
        if self.storage is None:
            return

        payload = self.storage.get(self.storage_key, {})
        if not isinstance(payload, dict):
            return

        players_state = payload.get("players", {})
        if not isinstance(players_state, dict):
            return

        self.auditor.import_state(players_state)

    def _persist_state(self) -> None:
        if self.storage is None:
            return

        payload = {
            "version": 1,
            "players": self.auditor.export_state(max_samples_per_player=self.max_samples_per_player),
        }
        # Use ttl=0 so RNG audit history survives across restarts.
        try:
            self.storage.set(self.storage_key, payload, ttl=0)  # type: ignore[call-arg]
        except TypeError:
            # Fallback for storage backends that don't support the ttl kwarg.
            self.storage.set(self.storage_key, payload)

    @staticmethod
    def _extract_float(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
        value = payload.get(key, default)
        if isinstance(value, (float, int)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        return default

    @staticmethod
    def _extract_bool(payload: dict[str, Any], key: str, default: bool = False) -> bool:
        value = payload.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def ingest_showdown(self, payload: dict[str, Any]) -> PlayerAuditStats | None:
        """Record a showdown result and return updated stats for the opponent."""
        opponent_id = self._normalize_player_id(str(payload.get("opponent_id", "")))
        if not opponent_id:
            return None

        equity = self._extract_float(payload, "equity", 0.0)
        won = self._extract_bool(payload, "won", False)
        self.auditor.add_allin_result(player_id=opponent_id, equity=equity, won=won)
        self._persist_state()
        return self.auditor.player_stats(opponent_id)

    def should_evade(self, opponent_id: str) -> RngAlert:
        """Check whether the agent should fold against *opponent_id*."""
        normalized_id = self._normalize_player_id(opponent_id)
        stats = self.auditor.player_stats(normalized_id)
        return RngAlert(
            opponent_id=normalized_id,
            should_evade=stats.is_super_user,
            z_score=stats.z_score,
            sample_count=stats.sample_count,
        )

    def flagged_opponents(self) -> list[str]:
        """Return a sorted list of all opponent ids flagged as super-users."""
        return self.auditor.super_users()

    def telemetry_summary(self, top_k: int = 3) -> dict[str, Any]:
        """Build a summary dict suitable for report / dashboard output."""
        player_stats = self.auditor.all_player_stats()
        if not player_stats:
            return {
                "players_audited": 0,
                "players_flagged": 0,
                "flagged_opponents": [],
                "top_zscores": [],
            }

        flagged = [stats.player_id for stats in player_stats if stats.is_super_user]
        top_count = max(1, int(top_k))
        top_zscores = sorted(player_stats, key=lambda stats: stats.z_score, reverse=True)[:top_count]

        return {
            "players_audited": len(player_stats),
            "players_flagged": len(flagged),
            "flagged_opponents": sorted(flagged),
            "top_zscores": [
                {
                    "player_id": stats.player_id,
                    "z_score": round(float(stats.z_score), 4),
                    "sample_count": int(stats.sample_count),
                    "is_super_user": bool(stats.is_super_user),
                }
                for stats in top_zscores
            ],
        }

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(slots=True)
class AuditSample:
    expected_value: float
    observed: float


class RngAuditor:
    def __init__(self) -> None:
        self._samples: dict[str, list[AuditSample]] = defaultdict(list)

    def add_sample(self, player_id: str, expected_value: float, observed: float) -> None:
        self._samples[player_id].append(AuditSample(expected_value, observed))

    def player_sample_count(self, player_id: str) -> int:
        return len(self._samples[player_id])

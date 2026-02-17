from __future__ import annotations

import os

from tools.vision_models import TableSnapshot


class MockVision:
    def __init__(self, scenario: str = "ALT") -> None:
        self.scenario = (scenario or "ALT").strip().upper()
        self._last_signature = ""
        self._call_count = 0

    def _snapshot_scenario_a(self) -> TableSnapshot:
        return TableSnapshot(
            hero_cards=["Ah", "Kh"],
            board_cards=["Th", "Jh", "2s"],
            pot=32.0,
            stack=118.0,
            call_amount=6.0,
            dead_cards=[],
            current_opponent="mock_villain",
            active_players=2,
            action_points={
                "fold": (620, 705),
                "call": (805, 705),
                "raise_small": (995, 705),
                "raise_big": (1085, 705),
            },
            showdown_events=[],
            is_my_turn=True,
            state_changed=False,
        )

    def _snapshot_scenario_b(self) -> TableSnapshot:
        return TableSnapshot(
            hero_cards=["7d", "2c"],
            board_cards=["Ah", "Kh", "Qs"],
            pot=32.0,
            stack=118.0,
            call_amount=6.0,
            dead_cards=[],
            current_opponent="mock_villain",
            active_players=2,
            action_points={
                "fold": (620, 705),
                "call": (805, 705),
                "raise_small": (995, 705),
                "raise_big": (1085, 705),
            },
            showdown_events=[],
            is_my_turn=True,
            state_changed=False,
        )

    def _state_signature(self, snapshot: TableSnapshot) -> str:
        hero_key = ",".join(snapshot.hero_cards)
        board_key = ",".join(snapshot.board_cards)
        dead_key = ",".join(snapshot.dead_cards)
        action_key = "|".join(
            f"{name}:{point[0]},{point[1]}"
            for name, point in sorted(snapshot.action_points.items(), key=lambda item: item[0])
        )
        return "|".join([
            hero_key,
            board_key,
            dead_key,
            f"pot={snapshot.pot:.2f}",
            f"stack={snapshot.stack:.2f}",
            f"call={snapshot.call_amount:.2f}",
            f"turn={1 if snapshot.is_my_turn else 0}",
            f"ap={action_key}",
        ])

    def read_table(self) -> TableSnapshot:
        scenario = self.scenario
        self._call_count += 1

        if scenario == "A":
            snapshot = self._snapshot_scenario_a()
        elif scenario == "B":
            snapshot = self._snapshot_scenario_b()
        else:
            if self._call_count % 2 == 1:
                snapshot = self._snapshot_scenario_a()
            else:
                snapshot = self._snapshot_scenario_b()

        signature = self._state_signature(snapshot)
        snapshot.state_changed = bool(self._last_signature) and signature != self._last_signature
        self._last_signature = signature
        return snapshot


def use_mock_vision_from_env() -> bool:
    return os.getenv("TITAN_USE_MOCK_VISION", "0").strip().lower() in {"1", "true", "yes", "on"}

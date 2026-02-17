"""Mock Vision — visão simulada para testes sem emulador real.

Fornece cenários determinísticos de :class:`TableSnapshot` para validar
a pipeline de decisão sem depender do YOLO, OCR ou emulador.

Cenários disponíveis:
    * **A** — Mão forte: ``Ah Kh`` no board ``Th Jh 2s`` (nut flush draw).
    * **B** — Mão lixo: ``7d 2c`` no board ``Ah Kh Qs`` (sem conexão).
    * **ALT** — Alterna entre A e B a cada chamada (teste de amnésia).

Uso::

    from agent.vision_mock import MockVision

    mock = MockVision(scenario="ALT")
    snap = mock.read_table()  # ciclo 1 → cenário A
    snap = mock.read_table()  # ciclo 2 → cenário B
"""

from __future__ import annotations

import os

from tools.vision_models import TableSnapshot


class MockVision:
    """Visão mock determinística para testes offline.

    Args:
        scenario: Cenário a usar — ``"A"``, ``"B"`` ou ``"ALT"`` (default).
    """

    def __init__(self, scenario: str = "ALT") -> None:
        self.scenario = (scenario or "ALT").strip().upper()
        self._last_signature = ""
        self._call_count = 0

    def _snapshot_scenario_a(self) -> TableSnapshot:
        """Cenário A — mão forte (Ah Kh com flush draw no flop)."""
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
        """Cenário B — mão lixo (7d 2c sem conexão ao board)."""
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
        """Gera assinatura única do estado para detecção de state_changed."""
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
        """Retorna o próximo TableSnapshot conforme o cenário configurado.

        Em modo ALT, alterna entre os cenários A e B a cada chamada.
        Detecta mudanças de estado via assinatura do snapshot anterior.

        Returns:
            TableSnapshot com ``state_changed=True`` se houve transição.
        """
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
    """Verifica se a variável ``TITAN_USE_MOCK_VISION`` está ativa."""
    return os.getenv("TITAN_USE_MOCK_VISION", "0").strip().lower() in {"1", "true", "yes", "on"}

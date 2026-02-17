from __future__ import annotations

from core.hive_brain import HiveBrain


def test_hive_brain_solo_then_squad_memory_mode() -> None:
    brain = HiveBrain(bind_address="tcp://127.0.0.1:5999", redis_url="redis://127.0.0.1:6399/0", ttl_seconds=10)

    first = brain._handle_checkin(
        {
            "agent_id": "A1",
            "table_id": "T1",
            "cards": ["As", "Kd", "Qc", "Jh", "Ts", "9d"],
            "active_players": 6,
        }
    )
    assert first["ok"] is True
    assert first["mode"] == "solo"
    assert first["partners"] == []

    second = brain._handle_checkin(
        {
            "agent_id": "A2",
            "table_id": "T1",
            "cards": ["Ac", "Kc", "Qd", "Jd", "Td", "9c"],
            "active_players": 2,
        }
    )
    assert second["ok"] is True
    assert second["mode"] == "squad"
    assert "A1" in second["partners"]
    assert second["heads_up_obfuscation"] is True


def test_hive_brain_normalizes_cards() -> None:
    cards = HiveBrain._normalize_cards(["10H", "ah", "AH", "zz", 123])
    assert cards == ["Th", "Ah"]

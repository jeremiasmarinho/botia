"""Agent configuration dataclass and environment-variable utilities.

Centralises all agent-level configuration so the main agent module stays
focused on the run loop.

Typical usage::

    config = AgentConfig(
        agent_id="bot_1",
        server_address="tcp://127.0.0.1:5555",
    )
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class AgentConfig:
    """Immutable configuration for a single :class:`PokerAgent` instance.

    Attributes:
        agent_id:         Unique identifier sent in ZMQ check-in messages.
        server_address:   ZMQ ``REQ`` endpoint (e.g. ``tcp://127.0.0.1:5555``).
        table_id:         Logical table identifier for multi-table support.
        interval_seconds: Sleep between decision cycles.
        timeout_ms:       ZMQ send/receive timeout.
        active_players:   Override for active player count (``None`` = auto).
        max_cycles:       Stop after this many cycles (``None`` = infinite).
        redis_url:        Connection URL for the Redis memory backend.
    """

    agent_id: str
    server_address: str
    table_id: str = "table_default"
    interval_seconds: float = 1.0
    timeout_ms: int = 1500
    active_players: int | None = None
    max_cycles: int | None = None
    redis_url: str = "redis://127.0.0.1:6379/0"


# ── Environment-variable parsing helpers ─────────────────────────────────

def parse_float_env(name: str, default: float) -> float:
    """Read a float from env-var *name*, returning *default* on absence or error."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_int_env(name: str, default: int) -> int:
    """Read an integer from env-var *name*, returning *default* on absence or error."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if raw.lstrip("-").isdigit():
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def clamp_float(value: float, min_value: float, max_value: float) -> float:
    """Clamp *value* into ``[min_value, max_value]``."""
    return max(min_value, min(max_value, value))


def clamp_int(value: int, min_value: int, max_value: int) -> int:
    """Clamp *value* into ``[min_value, max_value]``."""
    return max(min_value, min(max_value, value))

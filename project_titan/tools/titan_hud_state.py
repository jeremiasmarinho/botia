"""titan_hud_state.py — Thread-safe shared state between PokerAgent and HUD GUI.

The agent writes data here every cycle; the HUD GUI reads it at ~4 fps
and paints the interface.  A simple dataclass + threading.Lock is enough
(no ZMQ / pipe overhead).

Usage (agent side)::

    from tools.titan_hud_state import hud_state
    hud_state.push(hero_cards=["Ah","Kd"], equity=0.72, ...)

Usage (GUI side)::

    from tools.titan_hud_state import hud_state
    snap = hud_state.snapshot()   # returns a frozen dict
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _HudSnapshot:
    """Immutable snapshot of the current bot state for the HUD."""

    # ── Toggle ──
    bot_active: bool = False

    # ── Cards ──
    hero_cards: list[str] = field(default_factory=list)
    board_cards: list[str] = field(default_factory=list)
    dead_cards: list[str] = field(default_factory=list)

    # ── Table metrics ──
    pot: float = 0.0
    stack: float = 0.0
    call_amount: float = 0.0
    active_players: int = 0
    is_my_turn: bool = False

    # ── Decision ──
    action: str = "wait"
    street: str = "preflop"
    equity: float = 0.0
    spr: float = 99.0
    pot_odds: float = 0.0
    committed: bool = False
    mode: str = "SOLO"
    opponent_class: str = "Unknown"
    gto_distribution: dict[str, float] = field(default_factory=dict)
    description: str = ""

    # ── Performance ──
    cycle_id: int = 0
    cycle_ms: float = 0.0
    sanity_ok: bool = True
    sanity_reason: str = "ok"

    # ── Timing ──
    updated_at: float = 0.0

    # ── Action log (last N) ──
    action_log: list[str] = field(default_factory=list)


_MAX_LOG_ENTRIES = 50


class HudState:
    """Thread-safe mutable state container.

    The agent calls ``push()`` to update fields; the GUI calls
    ``snapshot()`` to obtain a consistent copy.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = _HudSnapshot()
        self._toggle_callback: Any = None

    # ── Agent-side API ─────────────────────────────────────────────

    def push(self, **kwargs: Any) -> None:
        """Update one or more fields atomically."""
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._data, k):
                    # Copy mutable collections
                    if isinstance(v, list):
                        v = list(v)
                    elif isinstance(v, dict):
                        v = dict(v)
                    setattr(self._data, k, v)
            self._data.updated_at = time.time()

    def log_action(self, entry: str) -> None:
        """Append to the rolling action log."""
        with self._lock:
            self._data.action_log.append(entry)
            if len(self._data.action_log) > _MAX_LOG_ENTRIES:
                self._data.action_log = self._data.action_log[-_MAX_LOG_ENTRIES:]

    # ── GUI-side API ───────────────────────────────────────────────

    def snapshot(self) -> _HudSnapshot:
        """Return a consistent copy of the current state."""
        with self._lock:
            return _HudSnapshot(
                bot_active=self._data.bot_active,
                hero_cards=list(self._data.hero_cards),
                board_cards=list(self._data.board_cards),
                dead_cards=list(self._data.dead_cards),
                pot=self._data.pot,
                stack=self._data.stack,
                call_amount=self._data.call_amount,
                active_players=self._data.active_players,
                is_my_turn=self._data.is_my_turn,
                action=self._data.action,
                street=self._data.street,
                equity=self._data.equity,
                spr=self._data.spr,
                pot_odds=self._data.pot_odds,
                committed=self._data.committed,
                mode=self._data.mode,
                opponent_class=self._data.opponent_class,
                gto_distribution=dict(self._data.gto_distribution),
                description=self._data.description,
                cycle_id=self._data.cycle_id,
                cycle_ms=self._data.cycle_ms,
                sanity_ok=self._data.sanity_ok,
                sanity_reason=self._data.sanity_reason,
                updated_at=self._data.updated_at,
                action_log=list(self._data.action_log),
            )

    # ── Toggle binding ─────────────────────────────────────────────

    def set_toggle_callback(self, callback: Any) -> None:
        """Register a callback ``(active: bool) -> None`` for the GUI toggle."""
        self._toggle_callback = callback

    def request_toggle(self, active: bool) -> None:
        """Called by the GUI when the user clicks ON/OFF."""
        with self._lock:
            self._data.bot_active = active
        if self._toggle_callback is not None:
            try:
                self._toggle_callback(active)
            except Exception:
                pass


# ── Module-level singleton ─────────────────────────────────────────
hud_state = HudState()

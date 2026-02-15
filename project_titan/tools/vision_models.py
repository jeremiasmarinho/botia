"""Data models for the YOLO vision pipeline.

Contains the immutable value objects exchanged between the vision layer and
the decision engine:

* :class:`TableSnapshot` — complete observed state of a poker table captured
  from a single YOLO inference frame.
* :class:`DetectionItem` — individual bounding-box detection with its label,
  confidence and screen coordinates.

Both dataclasses use ``slots=True`` for memory efficiency in hot loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TableSnapshot:
    """Immutable snapshot of everything the vision system observed on the table.

    Attributes:
        hero_cards:   Player's hole cards, e.g. ``["As", "Kd"]``.
        board_cards:  Community cards visible on the board.
        pot:          Current pot size (float, zero when unknown).
        stack:        Hero's remaining stack.
        dead_cards:   Cards known to be out of play (mucked / burned).
        current_opponent: Identifier string of the opponent in focus.
        active_players:   Number of players still in the hand.
        action_points:    Screen coordinates for each action button,
                          keyed by action name → ``(x, y)``.
        showdown_events:  Parsed showdown / all-in events with opponent_id,
                          equity and won/lost flag.
        is_my_turn:       Whether it is currently the hero's turn to act.
        state_changed:    ``True`` when the current snapshot differs from the
                          previous one (set by :meth:`VisionTool._mark_state_change`).
    """

    hero_cards: list[str]
    board_cards: list[str]
    pot: float
    stack: float
    dead_cards: list[str] = field(default_factory=list)
    current_opponent: str = ""
    active_players: int = 0
    action_points: dict[str, tuple[int, int]] = field(default_factory=dict)
    showdown_events: list[dict[str, Any]] = field(default_factory=list)
    is_my_turn: bool = False
    state_changed: bool = False


@dataclass(slots=True)
class DetectionItem:
    """Single YOLO bounding-box detection.

    Attributes:
        label:      Class name returned by the model.
        confidence: Prediction confidence in ``[0, 1]``.
        center_x:   Horizontal centre of the bounding box (pixels).
        center_y:   Vertical centre of the bounding box (pixels).
    """

    label: str
    confidence: float
    center_x: float
    center_y: float

"""ActionTool – bridge between the decision workflow and the GhostMouse actuator.

When ``TITAN_GHOST_MOUSE=1``, actions are executed via humanised Bézier
mouse movement.  Otherwise the tool only computes timing (safe for CI /
simulation runs).
"""

from __future__ import annotations

import os
from typing import Any

from agent.ghost_mouse import (
    ClickPoint,
    GhostMouse,
    GhostMouseConfig,
    classify_difficulty,
)


# Default screen regions (overridable via env / set_action_regions)
_DEFAULT_ACTION_REGIONS: dict[str, ClickPoint] = {
    "fold": ClickPoint(x=600, y=700),
    "call": ClickPoint(x=800, y=700),
    "raise_small": ClickPoint(x=1000, y=700),
    "raise_big": ClickPoint(x=1000, y=700),
}


class ActionTool:
    """Execute a poker action, optionally driving real cursor movement."""

    def __init__(self) -> None:
        self._ghost = GhostMouse(GhostMouseConfig())
        self._regions = dict(_DEFAULT_ACTION_REGIONS)
        self._load_regions_from_env()

    # -- configuration -------------------------------------------------------

    def set_action_regions(self, regions: dict[str, ClickPoint]) -> None:
        """Override button regions at runtime (e.g. from vision calibration)."""
        self._regions.update(regions)

    def set_action_regions_from_xy(self, regions: dict[str, tuple[int, int]]) -> None:
        """Override button regions from plain (x, y) tuples."""
        normalized: dict[str, ClickPoint] = {}
        for action_name, point in regions.items():
            if not isinstance(action_name, str):
                continue
            if not isinstance(point, tuple) or len(point) != 2:
                continue
            x_raw, y_raw = point
            if not isinstance(x_raw, int) or not isinstance(y_raw, int):
                continue
            normalized[action_name.strip().lower()] = ClickPoint(x=x_raw, y=y_raw)
        if normalized:
            self._regions.update(normalized)

    # -- public API ----------------------------------------------------------

    def act(self, action: str, street: str = "preflop") -> str:
        """Execute *action* and return a summary string.

        *street* is used to compute thinking-delay difficulty.
        """
        action_lower = action.strip().lower()
        difficulty = classify_difficulty(action_lower, street)
        target = self._regions.get(action_lower)

        if target is not None:
            delay = self._ghost.move_and_click(target, difficulty=difficulty)
        else:
            delay = self._ghost._thinking_delay(difficulty)

        return f"action={action} delay={delay:.2f}s difficulty={difficulty}"

    # -- helpers -------------------------------------------------------------

    def _load_regions_from_env(self) -> None:
        """Load ``TITAN_BTN_FOLD``, ``TITAN_BTN_CALL``, etc. from env."""
        mapping = {
            "fold": "TITAN_BTN_FOLD",
            "call": "TITAN_BTN_CALL",
            "raise_small": "TITAN_BTN_RAISE_SMALL",
            "raise_big": "TITAN_BTN_RAISE_BIG",
        }
        for action_name, env_key in mapping.items():
            raw = os.getenv(env_key, "").strip()
            if "," in raw:
                parts = raw.split(",", 1)
                try:
                    self._regions[action_name] = ClickPoint(x=int(parts[0]), y=int(parts[1]))
                except ValueError:
                    pass

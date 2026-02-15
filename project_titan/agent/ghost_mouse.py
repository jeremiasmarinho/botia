"""Ghost Mouse – humanised cursor movement with Bézier curves.

Implements the Ghost Protocol from the Project Titan spec:
  • Bézier curves (never move in a straight line).
  • Noise injection (small random arcs).
  • Variable timing by decision complexity.
  • Optional PyAutoGUI backend for real cursor control.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from random import gauss, randint, uniform
from typing import Sequence

try:
    import pyautogui  # type: ignore[import-untyped]

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.0
    _HAS_PYAUTOGUI = True
except Exception:
    pyautogui = None  # type: ignore[assignment]
    _HAS_PYAUTOGUI = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ClickPoint:
    x: int
    y: int


@dataclass(slots=True)
class CurvePoint:
    x: float
    y: float


@dataclass(slots=True)
class GhostMouseConfig:
    """Tunables for humanised movement."""

    # Bézier generation
    control_point_spread: float = 0.35  # max % of distance for control-point offset
    noise_amplitude: float = 3.0  # px noise added to each interpolated point
    steps_per_100px: int = 18  # interpolation density

    # Timing (seconds) by decision difficulty
    timing_easy: tuple[float, float] = (0.8, 1.5)
    timing_medium: tuple[float, float] = (2.0, 4.0)
    timing_hard: tuple[float, float] = (4.0, 12.0)

    # Click parameters
    click_hold_min: float = 0.04
    click_hold_max: float = 0.12

    # Movement duration (seconds per 100px distance)
    move_duration_per_100px: float = 0.06


# ---------------------------------------------------------------------------
# Bézier maths
# ---------------------------------------------------------------------------

def _bezier_point(t: float, p0: CurvePoint, p1: CurvePoint, p2: CurvePoint, p3: CurvePoint) -> CurvePoint:
    """Evaluate a cubic Bézier curve at parameter *t* ∈ [0, 1]."""
    u = 1.0 - t
    u2 = u * u
    t2 = t * t
    coeff0 = u2 * u
    coeff1 = 3.0 * u2 * t
    coeff2 = 3.0 * u * t2
    coeff3 = t2 * t
    return CurvePoint(
        x=coeff0 * p0.x + coeff1 * p1.x + coeff2 * p2.x + coeff3 * p3.x,
        y=coeff0 * p0.y + coeff1 * p1.y + coeff2 * p2.y + coeff3 * p3.y,
    )


def _generate_bezier_path(
    start: CurvePoint,
    end: CurvePoint,
    spread: float = 0.35,
    noise_amp: float = 3.0,
    density: int = 18,
) -> list[CurvePoint]:
    """Return a list of waypoints along a noisy cubic Bézier from *start* to *end*."""
    dx = end.x - start.x
    dy = end.y - start.y
    distance = math.hypot(dx, dy)

    # Number of interpolation steps proportional to distance
    num_steps = max(int(distance / 100.0 * density), 8)

    # Random control points, offset perpendicular to the straight line
    max_offset = max(distance * spread, 20.0)
    cp1 = CurvePoint(
        x=start.x + dx * uniform(0.2, 0.45) + uniform(-max_offset, max_offset),
        y=start.y + dy * uniform(0.2, 0.45) + uniform(-max_offset, max_offset),
    )
    cp2 = CurvePoint(
        x=start.x + dx * uniform(0.55, 0.8) + uniform(-max_offset, max_offset),
        y=start.y + dy * uniform(0.55, 0.8) + uniform(-max_offset, max_offset),
    )

    path: list[CurvePoint] = []
    for i in range(num_steps + 1):
        t = i / num_steps
        pt = _bezier_point(t, start, cp1, cp2, end)
        # Add Gaussian noise (except at exact endpoints)
        if 0 < i < num_steps:
            pt = CurvePoint(
                x=pt.x + gauss(0, noise_amp),
                y=pt.y + gauss(0, noise_amp),
            )
        path.append(pt)

    return path


# ---------------------------------------------------------------------------
# Decision-difficulty classifier
# ---------------------------------------------------------------------------

_DIFFICULTY_EASY = "easy"
_DIFFICULTY_MEDIUM = "medium"
_DIFFICULTY_HARD = "hard"


def classify_difficulty(action: str, street: str = "preflop") -> str:
    """Infer decision difficulty from the chosen action and street.

    Spec references:
        Easy  (preflop fold): 0.8 – 1.5 s
        Hard  (river bluff):  4.0 – 12.0 s
    """
    action_lower = action.strip().lower()

    if action_lower == "fold" and street == "preflop":
        return _DIFFICULTY_EASY

    if action_lower in {"raise_big", "raise_small"} and street in {"turn", "river"}:
        return _DIFFICULTY_HARD

    if action_lower == "fold" and street in {"turn", "river"}:
        return _DIFFICULTY_MEDIUM

    if action_lower in {"raise_big", "raise_small"}:
        return _DIFFICULTY_MEDIUM

    # call anywhere, fold on flop, etc.
    return _DIFFICULTY_EASY


# ---------------------------------------------------------------------------
# GhostMouse
# ---------------------------------------------------------------------------

class GhostMouse:
    """Humanised mouse controller with Bézier curves and variable timing."""

    def __init__(self, config: GhostMouseConfig | None = None) -> None:
        self.config = config or GhostMouseConfig()
        self._enabled = _HAS_PYAUTOGUI and os.getenv("TITAN_GHOST_MOUSE", "0").strip().lower() in {"1", "true", "yes", "on"}

    # -- public API ----------------------------------------------------------

    def move_and_click(self, point: ClickPoint, difficulty: str = _DIFFICULTY_EASY) -> float:
        """Move the cursor to *point* along a Bézier curve, click, and return the thinking delay (seconds).

        If PyAutoGUI is unavailable or ``TITAN_GHOST_MOUSE`` is not enabled,
        the method still computes and returns the delay without moving the
        real cursor (safe for testing / CI).
        """
        delay = self._thinking_delay(difficulty)

        if self._enabled and pyautogui is not None:
            self._execute_move_and_click(point)

        return delay

    def compute_path(self, start: ClickPoint, end: ClickPoint) -> list[CurvePoint]:
        """Return the Bézier waypoints without executing movement (useful for debugging / tests)."""
        return _generate_bezier_path(
            CurvePoint(start.x, start.y),
            CurvePoint(end.x, end.y),
            spread=self.config.control_point_spread,
            noise_amp=self.config.noise_amplitude,
            density=self.config.steps_per_100px,
        )

    # -- internal helpers ----------------------------------------------------

    def _thinking_delay(self, difficulty: str) -> float:
        """Return a random delay matching the decision difficulty."""
        if difficulty == _DIFFICULTY_HARD:
            lo, hi = self.config.timing_hard
        elif difficulty == _DIFFICULTY_MEDIUM:
            lo, hi = self.config.timing_medium
        else:
            lo, hi = self.config.timing_easy
        return uniform(lo, hi)

    def _execute_move_and_click(self, target: ClickPoint) -> None:
        """Perform actual Bézier mouse movement + click via PyAutoGUI."""
        assert pyautogui is not None

        current_x, current_y = pyautogui.position()
        path = _generate_bezier_path(
            CurvePoint(current_x, current_y),
            CurvePoint(target.x, target.y),
            spread=self.config.control_point_spread,
            noise_amp=self.config.noise_amplitude,
            density=self.config.steps_per_100px,
        )

        # Calculate total movement duration
        distance = math.hypot(target.x - current_x, target.y - current_y)
        total_duration = max(distance / 100.0 * self.config.move_duration_per_100px, 0.05)
        step_pause = total_duration / max(len(path), 1)

        for pt in path:
            pyautogui.moveTo(int(pt.x), int(pt.y), _pause=False)
            pyautogui.sleep(step_pause)

        # Hold click for a human-like duration
        hold = uniform(self.config.click_hold_min, self.config.click_hold_max)
        pyautogui.click(_pause=False)
        pyautogui.sleep(hold)

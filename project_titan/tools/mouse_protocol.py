"""Shared data types and helpers for mouse interaction.

This module breaks the ``tools → agent`` layer inversion by holding the
data classes and pure classifiers that both :mod:`tools.action_tool` and
:mod:`agent.ghost_mouse` depend on.  Neither layer imports the other for
these definitions.
"""

from __future__ import annotations

from dataclasses import dataclass


# Re-export data types ──────────────────────────────────────────────────────

@dataclass(slots=True)
class ClickPoint:
    x: int
    y: int


@dataclass(slots=True)
class GhostMouseConfig:
    """Tunables for humanised movement."""

    # Bézier generation
    control_point_spread: float = 0.35
    noise_amplitude: float = 3.0
    steps_per_100px: int = 18

    # Timing (seconds) by decision difficulty
    timing_easy: tuple[float, float] = (0.8, 1.5)
    timing_medium: tuple[float, float] = (2.0, 4.0)
    timing_hard: tuple[float, float] = (4.0, 12.0)

    # Click parameters — log-normal distribution
    click_hold_mu: float = -2.7
    click_hold_sigma: float = 0.35
    click_hold_min: float = 0.03
    click_hold_max: float = 0.25
    click_jitter_px: float = 2.0

    # Movement duration (seconds per 100px distance)
    move_duration_per_100px: float = 0.06

    # Velocity curve — ease-in/ease-out parameters
    velocity_curve_enabled: bool = True
    velocity_ease_strength: float = 2.2

    # Micro-overshoot — human correction
    overshoot_probability: float = 0.12
    overshoot_distance_px: tuple[float, float] = (5.0, 14.0)
    overshoot_correction_ms: tuple[float, float] = (40.0, 120.0)

    # Idle jitter — micro-movements between actions
    idle_jitter_enabled: bool = True
    idle_jitter_amplitude_px: float = 4.0
    idle_jitter_interval: tuple[float, float] = (0.8, 3.0)

    # Poisson reaction delay
    poisson_delay_enabled: bool = True
    poisson_lambda_easy: float = 1.2
    poisson_lambda_medium: float = 3.0
    poisson_lambda_hard: float = 7.0


# Decision-difficulty classifier ───────────────────────────────────────────

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

    if action_lower in {"raise_big", "raise_small", "raise_pot"} and street in {"turn", "river"}:
        return _DIFFICULTY_HARD

    if action_lower == "fold" and street in {"turn", "river"}:
        return _DIFFICULTY_MEDIUM

    if action_lower in {"raise_big", "raise_small", "raise_pot", "raise_2x"}:
        return _DIFFICULTY_MEDIUM

    return _DIFFICULTY_EASY

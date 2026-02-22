"""Action selection via equity thresholds.

This module contains the decision logic that maps a computed equity score
to one of four poker actions (``fold``, ``call``, ``raise_small``,
``raise_big``).

The thresholds are **dynamically adjusted** based on:

* **Street** — preflop / flop / turn / river base thresholds.
* **Table profile** — tight / normal / aggressive offsets per street.
* **Table position** — UTG through BB offsets per street.
* **Opponents count** — multiway penalty (more opponents → tighter).
* **Pot odds** — adjusts call and raise thresholds.
* **SPR** (stack-to-pot ratio) — low-SPR loosens; high-SPR tightens.
* **Information quality** — fewer observed cards → penalty.

After all adjustments, final thresholds are clamped to prevent degenerate
values and maintain monotonicity (call < raise_small < raise_big).

Maintainer notes
-----------------
* When tuning thresholds, modify only the constant dicts at the top of
  :func:`select_action`.  The adjustment pipeline reads top-down.
* The ``_information_quality`` metric is intentionally simple; if a more
  sophisticated model is needed, replace it without altering the threshold
  pipeline.
"""

from __future__ import annotations

from utils.card_utils import pot_odds as _pot_odds, spr as _spr


def information_quality(
    hero_cards: list[str],
    board_cards: list[str],
    dead_cards: list[str],
) -> float:
    """Score the amount of information available to the decision engine.

    The ratio ``observed_cards / 12`` is a rough proxy — at 12 observed
    cards (e.g. 6 hero + 5 board + 1 dead) we consider full information.

    Returns:
        Float in ``[0.0, 1.0]``.
    """
    observed = len(hero_cards) + len(board_cards) + len(dead_cards)
    return min(max(observed / 12.0, 0.0), 1.0)


def select_action(
    win_rate: float,
    tie_rate: float,
    street: str,
    pot: float,
    stack: float,
    info_quality: float,
    table_profile: str,
    table_position: str,
    opponents_count: int,
) -> tuple[str, float, float]:
    """Choose a poker action based on equity and contextual adjustments.

    Args:
        win_rate:         Monte-Carlo win probability in ``[0, 1]``.
        tie_rate:         Monte-Carlo tie probability in ``[0, 1]``.
        street:           ``"preflop"`` / ``"flop"`` / ``"turn"`` / ``"river"``.
        pot:              Current pot size.
        stack:            Hero's remaining stack.
        info_quality:     Information quality score from :func:`information_quality`.
        table_profile:    ``"tight"`` / ``"normal"`` / ``"aggressive"``.
        table_position:   ``"utg"`` / ``"mp"`` / ``"co"`` / ``"btn"`` / ``"sb"`` / ``"bb"``.
        opponents_count:  Number of remaining opponents.

    Returns:
        Tuple of ``(action, score, pot_odds)`` where *action* is one of
        ``"fold"``, ``"call"``, ``"raise_small"``, ``"raise_big"``.
    """
    # ── 1. Base thresholds (call, raise_small, raise_big) per street ──
    base_thresholds: dict[str, tuple[float, float, float]] = {
        "preflop": (0.40, 0.62, 0.75),
        "flop":    (0.44, 0.65, 0.78),
        "turn":    (0.47, 0.69, 0.82),
        "river":   (0.50, 0.72, 0.85),
    }
    call_threshold, raise_small_threshold, raise_big_threshold = base_thresholds.get(
        street, (0.44, 0.65, 0.78)
    )

    # Combined score: wins count fully, ties count half.
    score = win_rate + (tie_rate * 0.5)
    pot_odds = _pot_odds(pot, stack)
    spr = _spr(pot, stack)

    # ── 2. Profile offsets (tight raises thresholds, aggressive lowers) ──
    profile_offsets_by_street: dict[str, dict[str, tuple[float, float, float]]] = {
        "tight": {
            "preflop": (0.05, 0.06, 0.06),
            "flop":    (0.04, 0.05, 0.05),
            "turn":    (0.04, 0.05, 0.05),
            "river":   (0.03, 0.04, 0.04),
        },
        "normal": {
            "preflop": (0.0, 0.0, 0.0),
            "flop":    (0.0, 0.0, 0.0),
            "turn":    (0.0, 0.0, 0.0),
            "river":   (0.0, 0.0, 0.0),
        },
        "aggressive": {
            "preflop": (-0.04, -0.05, -0.05),
            "flop":    (-0.03, -0.04, -0.04),
            "turn":    (-0.03, -0.04, -0.04),
            "river":   (-0.02, -0.03, -0.03),
        },
    }
    profile_offsets = profile_offsets_by_street.get(table_profile, profile_offsets_by_street["normal"])
    call_off, raise_s_off, raise_b_off = profile_offsets.get(street, (0.0, 0.0, 0.0))
    call_threshold += call_off
    raise_small_threshold += raise_s_off
    raise_big_threshold += raise_b_off

    # ── 3. Position offsets (early position tighter, late position looser) ──
    position_offsets_by_street: dict[str, dict[str, tuple[float, float, float]]] = {
        "utg": {
            "preflop": (0.04, 0.05, 0.05), "flop": (0.02, 0.03, 0.03),
            "turn":    (0.02, 0.03, 0.03), "river": (0.01, 0.02, 0.02),
        },
        "mp": {
            "preflop": (0.02, 0.02, 0.02), "flop": (0.01, 0.01, 0.01),
            "turn":    (0.01, 0.01, 0.01), "river": (0.0, 0.0, 0.0),
        },
        "co": {
            "preflop": (-0.02, -0.03, -0.03), "flop": (-0.01, -0.02, -0.02),
            "turn":    (-0.01, -0.01, -0.01), "river": (0.0, -0.01, -0.01),
        },
        "btn": {
            "preflop": (-0.04, -0.05, -0.05), "flop": (-0.02, -0.03, -0.03),
            "turn":    (-0.02, -0.02, -0.02), "river": (-0.01, -0.01, -0.01),
        },
        "sb": {
            "preflop": (0.03, 0.03, 0.03), "flop": (0.01, 0.01, 0.01),
            "turn":    (0.01, 0.01, 0.01), "river": (0.0, 0.0, 0.0),
        },
        "bb": {
            "preflop": (0.0, 0.0, 0.0),    "flop": (-0.01, -0.01, -0.01),
            "turn":    (-0.01, -0.01, -0.01), "river": (-0.01, -0.01, -0.01),
        },
    }
    position_offsets = position_offsets_by_street.get(table_position, position_offsets_by_street["mp"])
    pos_call, pos_raise_s, pos_raise_b = position_offsets.get(street, (0.0, 0.0, 0.0))
    call_threshold += pos_call
    raise_small_threshold += pos_raise_s
    raise_big_threshold += pos_raise_b

    # ── 4. Multiway adjustment (more opponents → more conservative) ──
    multiway_factor = max(0, opponents_count - 1)
    call_threshold += min(multiway_factor * 0.015, 0.07)
    raise_small_threshold += min(multiway_factor * 0.02, 0.10)
    raise_big_threshold += min(multiway_factor * 0.025, 0.12)

    # ── 5. Pot-odds adjustment ──────────────────────────────────────
    # Better pot odds → hero gets a better price to continue → LOWER
    # thresholds (encourage calls/raises when the pot is laying good odds).
    call_threshold -= min(pot_odds * 0.35, 0.08)
    raise_small_threshold -= min(pot_odds * 0.15, 0.05)
    raise_big_threshold -= min(pot_odds * 0.10, 0.04)

    # ── 6. SPR adjustment (committed stacks → loosen; deep → tighten) ──
    if street in {"turn", "river"} and spr <= 2.5:
        call_threshold -= 0.02
        raise_small_threshold -= 0.02
        raise_big_threshold -= 0.03
    elif street in {"preflop", "flop"} and spr >= 8.0:
        raise_small_threshold += 0.01
        raise_big_threshold += 0.02

    # ── 7. Heads-up steal bonus ─────────────────────────────────────
    if opponents_count == 1 and table_position in {"co", "btn"}:
        raise_small_threshold -= 0.015
        if street in {"turn", "river"}:
            raise_big_threshold -= 0.015

    # ── 8. First clamp (monotonicity) ──────────────────────────────
    call_threshold = min(max(call_threshold, 0.25), 0.90)
    raise_small_threshold = min(max(raise_small_threshold, call_threshold + 0.02), 0.94)
    raise_big_threshold = min(max(raise_big_threshold, raise_small_threshold + 0.03), 0.97)

    # ── 9. Information penalty (fewer cards seen → raise thresholds) ──
    information_penalty = max(0.0, 1.0 - info_quality) * 0.06
    call_threshold += information_penalty
    raise_small_threshold += information_penalty * 0.8
    raise_big_threshold += information_penalty * 0.5

    # ── 10. Final clamp ────────────────────────────────────────────
    call_threshold = min(max(call_threshold, 0.25), 0.92)
    raise_small_threshold = min(max(raise_small_threshold, call_threshold + 0.02), 0.96)
    raise_big_threshold = min(max(raise_big_threshold, raise_small_threshold + 0.03), 0.99)

    # ── 11. Decision ───────────────────────────────────────────────
    if score >= raise_big_threshold:
        return "raise_big", score, pot_odds
    if score >= raise_small_threshold:
        return "raise_small", score, pot_odds
    if score >= call_threshold:
        return "call", score, pot_odds
    return "fold", score, pot_odds

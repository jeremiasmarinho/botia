"""Label parsing and normalisation utilities for YOLO detections.

This module extracts structured poker-table information from raw YOLO class
names.  The parsing pipeline supports:

1. **User-defined aliases** — loaded from a JSON map file or env-var so
   custom YOLO datasets map cleanly to canonical tokens.
2. **Profile aliases** — regex-based rewriting for known dataset naming
   conventions (e.g. ``dataset_v1``).
3. **Category classification** — every raw label is classified into one of
   ``hero``, ``board``, ``dead``, ``pot``, ``stack``, ``generic_card``, or
   one of the specialised parsers (opponent, showdown, turn, active-players,
   action-button).

All functions are deterministic and stateless except for the thin caching of
``_unknown_labels`` used for debug logging.

Maintainer notes
-----------------
* When a new YOLO dataset introduces a novel naming convention, add a branch
  inside :func:`apply_profile_alias` rather than polluting the main
  :func:`parse_label` function.
* Regex patterns are compiled implicitly on first call by Python's ``re``
  cache.  For truly hot paths consider ``re.compile`` at module level.
"""

from __future__ import annotations

import re
from typing import Any

from tools.vision_constants import (
    CARD_TOKEN_RANKS,
    CARD_TOKEN_SUITS,
    RANK_WORD_MAP,
    SUIT_WORD_MAP,
)


# ── Card token helpers ──────────────────────────────────────────────────────

def is_card_label(label: str) -> bool:
    """Return ``True`` when *label* is exactly a two-character card token
    like ``"As"`` or ``"Td"``."""
    if len(label) != 2:
        return False
    return label[0] in CARD_TOKEN_RANKS and label[1] in CARD_TOKEN_SUITS


def normalize_card_token(token: str) -> str | None:
    """Attempt to convert a free-form *token* into a canonical two-char card.

    Supports:
    * Direct tokens (``"As"``, ``"10d"`` → ``"Td"``).
    * Underscore-separated words (``"ace_spades"`` → ``"As"``).

    Returns:
        Canonical card string or ``None`` when the token cannot be parsed.
    """
    cleaned = token.strip().replace("10", "T").replace("_", "").replace("-", "")
    if len(cleaned) < 2:
        return None

    # Fast path: look for a rank+suit pair inside the cleaned string.
    match = re.search(r"([2-9TJQKA])([CDHScdhs])", cleaned)
    if match is not None:
        rank = match.group(1).upper()
        suit = match.group(2).lower()
        normalized = f"{rank}{suit}"
        if is_card_label(normalized):
            return normalized

    # Slow path: try word-based parsing (e.g. "ace_spades").
    lowered = token.strip().lower().replace("-", "_")
    chunks = [chunk for chunk in lowered.split("_") if chunk]
    if not chunks:
        return None

    rank_candidate = ""
    suit_candidate = ""
    for chunk in chunks:
        if not rank_candidate and chunk in RANK_WORD_MAP:
            rank_candidate = RANK_WORD_MAP[chunk]
        if not suit_candidate and chunk in SUIT_WORD_MAP:
            suit_candidate = SUIT_WORD_MAP[chunk]

    if rank_candidate and suit_candidate:
        word_based = f"{rank_candidate}{suit_candidate}"
        if is_card_label(word_based):
            return word_based

    return None


# ── Alias resolution ────────────────────────────────────────────────────────

def apply_alias(label: str, aliases: dict[str, str], profile: str) -> str:
    """Resolve a raw YOLO label through user aliases then profile aliases.

    Args:
        label:    Raw label string from the YOLO model.
        aliases:  User-defined ``{raw_label: canonical_label}`` map.
        profile:  Active label profile (e.g. ``"generic"``, ``"dataset_v1"``).

    Returns:
        Canonical label string ready for category classification.
    """
    alias = aliases.get(label.strip().lower())
    if alias is not None:
        return alias
    return apply_profile_alias(label, profile)


def apply_profile_alias(label: str, profile: str) -> str:
    """Apply regex-based rewriting for a specific dataset naming convention.

    Currently supports:
    * ``dataset_v1`` / ``dataset-1`` / ``yolo_dataset_v1``

    Args:
        label:   Raw label string.
        profile: Active label profile name.

    Returns:
        Rewritten label or the original label unchanged.
    """
    if profile not in {"dataset_v1", "dataset-1", "yolo_dataset_v1"}:
        return label

    normalized = label.strip().lower().replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_\-.]", "", normalized)
    normalized = normalized.replace("-", "_")
    normalized = re.sub(r"_+", "_", normalized)

    # Hero card: e.g. "hero_card_1_As" or "player_card_As"
    hero_match = re.match(
        r"^(hero|player|my)_card(?:_\d+)?_([0-9tjqka]{1,2}[cdhs])$", normalized
    )
    if hero_match is not None:
        card_token = normalize_card_token(hero_match.group(2))
        if card_token is not None:
            return f"hero_{card_token}"

    # Board card: e.g. "board_card_1_Kd" or "flop_Kd"
    board_match = re.match(
        r"^(?:table_)?(?:board|flop|turn|river)(?:_card)?(?:_\d+)?_([0-9tjqka]{1,2}[cdhs])$",
        normalized,
    )
    if board_match is not None:
        card_token = normalize_card_token(board_match.group(1))
        if card_token is not None:
            return f"board_{card_token}"

    # Dead card: e.g. "burn_card_1_7h" or "muck_7h"
    dead_match = re.match(
        r"^(?:burn|dead|muck|folded)(?:_card)?(?:_\d+)?_([0-9tjqka]{1,2}[cdhs])$",
        normalized,
    )
    if dead_match is not None:
        card_token = normalize_card_token(dead_match.group(1))
        if card_token is not None:
            return f"dead_{card_token}"

    # Dead card by keyword presence (e.g. "burned_As")
    dead_tokens = {
        "burn", "burned", "dead", "muck", "mucked",
        "folded", "discard", "discarded", "gone",
    }
    dead_parts = [part for part in normalized.split("_") if part]
    if dead_parts and any(part in dead_tokens for part in dead_parts):
        for part in dead_parts:
            card_token = normalize_card_token(part)
            if card_token is not None:
                return f"dead_{card_token}"
        full_card_token = normalize_card_token(normalized)
        if full_card_token is not None:
            return f"dead_{full_card_token}"

    # Pot value: e.g. "pot_45.0"
    pot_match = re.match(r"^(?:pot|pote)(?:_value)?_([0-9]+(?:\.[0-9]+)?)$", normalized)
    if pot_match is not None:
        return f"pot_{pot_match.group(1)}"

    # Stack value: e.g. "hero_stack_200"
    stack_match = re.match(
        r"^(?:hero_stack|stack|my_stack)(?:_value)?_([0-9]+(?:\.[0-9]+)?)$", normalized
    )
    if stack_match is not None:
        return f"hero_stack_{stack_match.group(1)}"

    # Opponent identifier: e.g. "opponent_abc123"
    opponent_match = re.match(
        r"^(?:opponent|opp|villain)(?:_id)?_([a-z0-9_]+)$", normalized
    )
    if opponent_match is not None:
        return f"opponent_{opponent_match.group(1)}"

    # Showdown event: pass through as-is (downstream parser handles it)
    showdown_match = re.match(
        r"^(?:showdown|sd|allin)_([a-z0-9_]+)_(?:eq|equity)_([0-9]+(?:\.[0-9]+)?)(p)?_(?:won|win|lost|lose)$",
        normalized,
    )
    if showdown_match is not None:
        return normalized

    return label


# ── Category classification ─────────────────────────────────────────────────

def parse_label(
    label: str,
    aliases: dict[str, str],
    profile: str,
    debug_labels: bool,
    unknown_labels: set[str],
) -> tuple[str | None, str | None, float | None]:
    """Classify a raw YOLO label into a semantic category.

    Returns:
        Tuple of ``(category, card_token, numeric_value)`` where *category*
        is one of ``"hero"``, ``"board"``, ``"dead"``, ``"generic_card"``,
        ``"pot"``, ``"stack"``; and at most one of *card_token* or
        *numeric_value* is non-``None``.  All three are ``None`` when the
        label is unrecognised.
    """
    normalized = apply_alias(label, aliases, profile).strip()
    lowered = normalized.lower()
    direct_card = normalize_card_token(normalized)

    # ── Hero card patterns ──────────────────────────────────────────
    hero_patterns = [
        r"^(hero|hole|hand|player|my|pocket)[_\-]?(card)?[_\-]?(.+)$",
        r"^(h|hc)[_\-]?(\d+)?[_\-]?(.+)$",
    ]
    for pattern in hero_patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match is not None:
            candidate = normalize_card_token(match.group(match.lastindex or 1))
            return ("hero", candidate, None)

    # ── Board card patterns ─────────────────────────────────────────
    board_patterns = [
        r"^(board|flop|turn|river|community|table)[_\-]?(card)?[_\-]?(.+)$",
        r"^(b|bc)[_\-]?(\d+)?[_\-]?(.+)$",
    ]
    for pattern in board_patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match is not None:
            candidate = normalize_card_token(match.group(match.lastindex or 1))
            return ("board", candidate, None)

    # ── Dead card patterns ──────────────────────────────────────────
    dead_patterns = [
        r"^(dead|burn|muck|folded|gone)[_\-]?(card)?[_\-]?(.+)$",
        r"^(d|dc)[_\-]?(\d+)?[_\-]?(.+)$",
    ]
    for pattern in dead_patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match is not None:
            candidate = normalize_card_token(match.group(match.lastindex or 1))
            return ("dead", candidate, None)

    # ── Standalone card (no category prefix) ────────────────────────
    if direct_card is not None:
        return ("generic_card", direct_card, None)

    # ── Pot value ───────────────────────────────────────────────────
    pot_match = re.match(
        r"^(pot|pote|total_pot)[_\-]?([0-9]+(?:\.[0-9]+)?)$", lowered
    )
    if pot_match is not None:
        return ("pot", None, float(pot_match.group(2)))

    # ── Stack value ─────────────────────────────────────────────────
    stack_match = re.match(
        r"^(stack|hero_stack|my_stack)[_\-]?([0-9]+(?:\.[0-9]+)?)$", lowered
    )
    if stack_match is not None:
        return ("stack", None, float(stack_match.group(2)))

    # ── Unknown label (logged once for debugging) ───────────────────
    if debug_labels and normalized not in unknown_labels:
        unknown_labels.add(normalized)
        print(f"[VisionTool] unknown label: {normalized}")

    return (None, None, None)


# ── Specialised parsers ─────────────────────────────────────────────────────

def normalize_opponent_id(value: str) -> str:
    """Sanitise an opponent identifier to lowercase alphanumeric + underscores."""
    normalized = re.sub(r"[^a-zA-Z0-9_]", "", value.strip().replace("-", "_")).lower()
    return normalized


def parse_opponent_label(label: str, aliases: dict[str, str], profile: str) -> str | None:
    """Extract an opponent identifier from a YOLO label.

    Returns:
        Sanitised opponent id string, or ``None`` if the label is not an
        opponent label.
    """
    normalized = apply_alias(label, aliases, profile).strip().lower()
    patterns = [
        r"^(opponent|opp|villain|vilao)[_\-]?([a-zA-Z0-9_]+)$",
        r"^(opponent|opp|villain|vilao)[_\-]?(id)[_\-]?([a-zA-Z0-9_]+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        candidate = match.group(match.lastindex or 1)
        opponent_id = normalize_opponent_id(candidate)
        if opponent_id:
            return opponent_id
    return None


def normalize_equity(value: float, has_percent_suffix: bool) -> float:
    """Normalise an equity value to the ``[0, 1]`` range.

    Args:
        value:              Raw numeric equity value.
        has_percent_suffix: ``True`` when the label included a ``p`` suffix
                            indicating the value is a percentage.
    """
    equity = float(value)
    if has_percent_suffix or equity > 1.0:
        equity = equity / 100.0
    return min(max(equity, 0.0), 1.0)


def parse_showdown_label(
    label: str, aliases: dict[str, str], profile: str
) -> dict[str, Any] | None:
    """Parse a showdown / all-in event label.

    Expected patterns::

        showdown_<opponent>_eq_<equity>[p]_<won|lost>
        sd_<opponent>_<equity>[p]_<won|lost>

    Returns:
        Dict with ``opponent_id``, ``equity`` and ``won`` keys, or ``None``.
    """
    normalized = apply_alias(label, aliases, profile).strip().lower()
    patterns = [
        r"^(showdown|sd|allin)[_\-]([a-zA-Z0-9_]+)[_\-](?:eq|equity)[_\-]?([0-9]+(?:\.[0-9]+)?)(p)?[_\-](won|win|lost|lose)$",
        r"^(showdown|sd|allin)[_\-]([a-zA-Z0-9_]+)[_\-]([0-9]+(?:\.[0-9]+)?)(p)?[_\-](won|win|lost|lose)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        opponent_id = normalize_opponent_id(match.group(2))
        if not opponent_id:
            continue
        raw_equity = float(match.group(3))
        has_percent_suffix = match.group(4) == "p"
        equity = normalize_equity(raw_equity, has_percent_suffix)
        outcome_token = match.group(5)
        won = outcome_token in {"won", "win"}
        return {
            "opponent_id": opponent_id,
            "equity": round(equity, 4),
            "won": won,
        }
    return None


def parse_turn_label(label: str) -> bool | None:
    """Determine whether the hero should act based on a turn-indicator label.

    Returns:
        ``True`` (hero's turn), ``False`` (not hero's turn), or ``None``
        (label is not a turn indicator).
    """
    normalized = label.strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)

    positive_tokens = {
        "my_turn", "your_turn", "action_required", "act_now",
        "turn_on", "hero_turn", "btn_call", "btn_raise", "btn_fold",
    }
    negative_tokens = {
        "not_my_turn", "waiting_turn", "turn_off", "no_action",
    }

    if normalized in positive_tokens:
        return True
    if normalized in negative_tokens:
        return False
    return None


def parse_active_players_label(label: str) -> int | None:
    """Extract a player count from an active-players indicator label.

    Returns:
        Integer player count in ``[0, 10]``, or ``None`` if the label is
        not a player-count indicator.
    """
    normalized = label.strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_\\.]", "", normalized)

    patterns = [
        r"^(?:active_players|players_active|player_count|players_count|table_players|alive_players)_([0-9]{1,2})$",
        r"^(?:seats|players)_([0-9]{1,2})$",
        r"^(?:active|alive)_([0-9]{1,2})$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if 0 <= value <= 10:
            return value
    return None


def parse_action_button_label(label: str) -> str | None:
    """Map an action-button label to a canonical action name.

    Returns:
        One of ``"fold"``, ``"call"``, ``"raise_small"``, ``"raise_big"``
        or ``None`` when the label is not an action button.
    """
    normalized = label.strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)

    fold_patterns = [
        r"^(?:btn_)?fold(?:_button)?$",
        r"^(?:button_)?fold$",
        r"^action_fold$",
    ]
    call_patterns = [
        r"^(?:btn_)?call(?:_button)?$",
        r"^(?:button_)?call$",
        r"^action_call$",
        r"^check_call$",
        r"^check$",
    ]
    raise_patterns = [
        r"^(?:btn_)?raise(?:_button)?$",
        r"^(?:button_)?raise$",
        r"^action_raise$",
        r"^(?:btn_)?bet(?:_button)?$",
        r"^action_bet$",
    ]
    raise_big_patterns = [
        r"^(?:btn_)?allin(?:_button)?$",
        r"^(?:button_)?allin$",
        r"^action_allin$",
        r"^raise_big$",
    ]

    for pattern in fold_patterns:
        if re.match(pattern, normalized):
            return "fold"
    for pattern in call_patterns:
        if re.match(pattern, normalized):
            return "call"
    # raise_big must be checked before raise_small (more specific first)
    for pattern in raise_big_patterns:
        if re.match(pattern, normalized):
            return "raise_big"
    for pattern in raise_patterns:
        if re.match(pattern, normalized):
            return "raise_small"
    return None

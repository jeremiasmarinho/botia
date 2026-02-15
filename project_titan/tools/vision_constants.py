"""Shared constants for the YOLO-based vision pipeline.

This module centralises every token set, lookup table and regex-independent
constant used when parsing card labels detected by the YOLO model.

Maintainer notes
-----------------
* ``CARD_TOKEN_RANKS`` and ``CARD_TOKEN_SUITS`` define the valid canonical
  characters.  Any label that ultimately resolves to a ``rank + suit`` pair
  must map through these sets.
* ``RANK_WORD_MAP`` / ``SUIT_WORD_MAP`` allow natural-language tokens
  (e.g. ``"ace"`` → ``"A"``) so datasets with verbose names still parse
  correctly.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical single-character tokens for rank and suit
# ---------------------------------------------------------------------------

CARD_TOKEN_RANKS: set[str] = set("23456789TJQKA")
"""Valid single-character rank tokens (``2``–``9``, ``T``, ``J``, ``Q``, ``K``, ``A``)."""

CARD_TOKEN_SUITS: set[str] = set("cdhs")
"""Valid single-character suit tokens (``c``lubs, ``d``iamonds, ``h``earts, ``s``pades)."""

# ---------------------------------------------------------------------------
# Natural-language word → canonical character mappings
# ---------------------------------------------------------------------------

RANK_WORD_MAP: dict[str, str] = {
    "ace": "A",
    "king": "K",
    "queen": "Q",
    "jack": "J",
    "ten": "T",
    "t": "T",
    "nine": "9",
    "eight": "8",
    "seven": "7",
    "six": "6",
    "five": "5",
    "four": "4",
    "three": "3",
    "two": "2",
}
"""Maps written-out rank words to their canonical single-character tokens."""

SUIT_WORD_MAP: dict[str, str] = {
    "hearts": "h",
    "heart": "h",
    "diamonds": "d",
    "diamond": "d",
    "clubs": "c",
    "club": "c",
    "spades": "s",
    "spade": "s",
}
"""Maps written-out suit words to their canonical single-character tokens."""

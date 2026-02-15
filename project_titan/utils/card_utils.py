"""Card index encoding utilities.

Maps the standard 52-card deck to a flat ``[0, 51]`` integer space
for compact storage and fast lookup.

Encoding: ``index = rank_idx * 4 + suit_idx``
where ``RANKS = '23456789TJQKA'`` and ``SUITS = 'cdhs'``.
"""

from __future__ import annotations

RANKS = "23456789TJQKA"
"""Ordered rank characters (``2``–``A``). Index position is the rank id."""

SUITS = "cdhs"
"""Ordered suit characters (clubs, diamonds, hearts, spades)."""


def card_to_index(card: str) -> int:
    """Convert a two-character card (e.g. ``'As'``) to an integer index."""
    rank, suit = card[0], card[1]
    rank_idx = RANKS.index(rank)
    suit_idx = SUITS.index(suit)
    return rank_idx * len(SUITS) + suit_idx


def index_to_card(index: int) -> str:
    """Convert an integer index back to a two-character card string."""
    rank_idx, suit_idx = divmod(index, len(SUITS))
    return f"{RANKS[rank_idx]}{SUITS[suit_idx]}"

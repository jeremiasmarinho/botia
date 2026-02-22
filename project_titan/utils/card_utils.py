"""Card encoding, normalisation and display utilities.

Maps the standard 52-card deck to a flat ``[0, 51]`` integer space
for compact storage and fast lookup.

Encoding: ``index = rank_idx * 4 + suit_idx``
where ``RANKS = '23456789TJQKA'`` and ``SUITS = 'cdhs'``.

This module is the **single source of truth** for card-string helpers
used across ``core``, ``agent``, ``workflows`` and ``tools``.
"""

from __future__ import annotations

from typing import Any

RANKS = "23456789TJQKA"
"""Ordered rank characters (``2``–``A``). Index position is the rank id."""

SUITS = "cdhs"
"""Ordered suit characters (clubs, diamonds, hearts, spades)."""

_RANK_PT: dict[str, str] = {
    "A": "Ás", "K": "Rei", "Q": "Dama", "J": "Valete", "T": "Dez",
    "9": "Nove", "8": "Oito", "7": "Sete", "6": "Seis", "5": "Cinco",
    "4": "Quatro", "3": "Três", "2": "Dois",
}

_SUIT_PT: dict[str, str] = {
    "H": "Copas", "D": "Ouros", "C": "Paus", "S": "Espadas",
}


# ── Index encoding ────────────────────────────────────────────────


def card_to_index(card: str) -> int:
    """Convert a two-character card (e.g. ``'As'``) to an integer index.

    Raises ``ValueError`` if *card* contains invalid rank or suit.
    """
    if len(card) < 2:
        raise ValueError(f"Invalid card string: {card!r}")
    rank, suit = card[0], card[1]
    try:
        rank_idx = RANKS.index(rank)
        suit_idx = SUITS.index(suit)
    except ValueError:
        raise ValueError(f"Invalid card string: {card!r}") from None
    return rank_idx * len(SUITS) + suit_idx


def index_to_card(index: int) -> str:
    """Convert an integer index back to a two-character card string."""
    if not 0 <= index <= 51:
        raise ValueError(f"Card index out of range [0, 51]: {index}")
    rank_idx, suit_idx = divmod(index, len(SUITS))
    return f"{RANKS[rank_idx]}{SUITS[suit_idx]}"


# ── Normalisation (canonical ``Xs`` format) ───────────────────────


def normalize_card(card: str) -> str | None:
    """Normalise a card string to canonical ``Xs`` format.

    Accepts common variants like ``"10h"`` → ``"Th"``, ``"aS"`` → ``"As"``.
    Returns ``None`` if the input is not a valid card.
    """
    cleaned = card.strip().upper().replace("10", "T")
    if len(cleaned) != 2:
        return None
    rank = cleaned[0]
    suit = cleaned[1].lower()
    if rank not in RANKS or suit not in SUITS:
        return None
    return f"{rank}{suit}"


def normalize_cards(raw_cards: Any) -> list[str]:
    """Normalise and deduplicate a list of card strings.

    Accepts ``list[str]``, silently skips non-string / invalid items.
    """
    if not isinstance(raw_cards, list):
        return []
    cards: list[str] = []
    for item in raw_cards:
        if not isinstance(item, str):
            continue
        normalized = normalize_card(item)
        if normalized is not None and normalized not in cards:
            cards.append(normalized)
    return cards


def merge_dead_cards(*sources: list[str]) -> list[str]:
    """Merge and deduplicate dead cards from multiple sources."""
    merged: list[str] = []
    for source in sources:
        for card in source:
            normalized = normalize_card(card)
            if normalized is not None and normalized not in merged:
                merged.append(normalized)
    return merged


def street_from_board(board_cards: list[str]) -> str:
    """Infer the current street from the number of community cards."""
    count = len(board_cards)
    if count >= 5:
        return "river"
    if count == 4:
        return "turn"
    if count >= 3:
        return "flop"
    return "preflop"


# ── Display (Portuguese) ──────────────────────────────────────────


def card_to_pt(card: str) -> str | None:
    """Return the Portuguese display name for a card (e.g. ``"Ah"`` → ``"Ás de Copas"``)."""
    token = str(card or "").strip().upper().replace("10", "T")
    if len(token) != 2:
        return None
    rank = _RANK_PT.get(token[0])
    suit = _SUIT_PT.get(token[1].upper())
    if rank is None or suit is None:
        return None
    return f"{rank} de {suit}"


# ── Poker math helpers ────────────────────────────────────────────


def pot_odds(pot: float, stack: float) -> float:
    """Fraction of the total that must be risked to stay in the hand.

    ``pot / (pot + stack)``  — returns 0.0 when pot or stack is non-positive.
    """
    if pot <= 0 or stack <= 0:
        return 0.0
    return pot / max(pot + stack, 1e-6)


def spr(pot: float, stack: float) -> float:
    """Stack-to-pot ratio — high means deep-stacked, low means committed.

    Returns 99.0 when pot or stack is non-positive (ultra-deep sentinel).
    """
    if pot <= 0 or stack <= 0:
        return 99.0
    return stack / max(pot, 1e-6)

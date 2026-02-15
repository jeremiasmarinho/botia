from __future__ import annotations

RANKS = "23456789TJQKA"
SUITS = "cdhs"


def card_to_index(card: str) -> int:
    rank, suit = card[0], card[1]
    rank_idx = RANKS.index(rank)
    suit_idx = SUITS.index(suit)
    return rank_idx * len(SUITS) + suit_idx


def index_to_card(index: int) -> str:
    rank_idx, suit_idx = divmod(index, len(SUITS))
    return f"{RANKS[rank_idx]}{SUITS[suit_idx]}"

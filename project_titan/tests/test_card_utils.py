"""Tests for utils.card_utils — the single source of truth for card helpers."""

from __future__ import annotations

import pytest

from utils.card_utils import (
    card_to_index,
    card_to_pt,
    index_to_card,
    merge_dead_cards,
    normalize_card,
    normalize_cards,
    street_from_board,
)


# ── normalize_card ────────────────────────────────────────────────


class TestNormalizeCard:
    def test_standard(self) -> None:
        assert normalize_card("Ah") == "Ah"
        assert normalize_card("2c") == "2c"
        assert normalize_card("Ts") == "Ts"

    def test_case_insensitive(self) -> None:
        assert normalize_card("aH") == "Ah"
        assert normalize_card("kD") == "Kd"

    def test_ten_alias(self) -> None:
        assert normalize_card("10h") == "Th"
        assert normalize_card("10S") == "Ts"

    def test_whitespace(self) -> None:
        assert normalize_card("  Ah  ") == "Ah"

    def test_invalid_returns_none(self) -> None:
        assert normalize_card("XY") is None
        assert normalize_card("") is None
        assert normalize_card("A") is None
        assert normalize_card("Ahh") is None


# ── normalize_cards ───────────────────────────────────────────────


class TestNormalizeCards:
    def test_basic(self) -> None:
        result = normalize_cards(["Ah", "2c", "Ts"])
        assert result == ["Ah", "2c", "Ts"]

    def test_deduplication(self) -> None:
        result = normalize_cards(["Ah", "ah", "AH"])
        assert result == ["Ah"]

    def test_skips_invalid(self) -> None:
        result = normalize_cards(["Ah", 42, None, "XY", "Kd"])  # type: ignore[list-item]
        assert result == ["Ah", "Kd"]

    def test_non_list_returns_empty(self) -> None:
        assert normalize_cards("Ah") == []  # type: ignore[arg-type]
        assert normalize_cards(None) == []  # type: ignore[arg-type]


# ── merge_dead_cards ──────────────────────────────────────────────


class TestMergeDeadCards:
    def test_merge(self) -> None:
        result = merge_dead_cards(["Ah", "2c"], ["Ts", "Ah"])
        assert result == ["Ah", "2c", "Ts"]

    def test_empty(self) -> None:
        assert merge_dead_cards([], []) == []

    def test_single_source(self) -> None:
        assert merge_dead_cards(["Kd"]) == ["Kd"]


# ── street_from_board ─────────────────────────────────────────────


class TestStreetFromBoard:
    def test_preflop(self) -> None:
        assert street_from_board([]) == "preflop"

    def test_flop(self) -> None:
        assert street_from_board(["Ah", "2c", "Kd"]) == "flop"

    def test_turn(self) -> None:
        assert street_from_board(["Ah", "2c", "Kd", "Ts"]) == "turn"

    def test_river(self) -> None:
        assert street_from_board(["Ah", "2c", "Kd", "Ts", "7h"]) == "river"


# ── card_to_index / index_to_card ─────────────────────────────────


class TestCardIndexEncoding:
    def test_roundtrip(self) -> None:
        for i in range(52):
            assert card_to_index(index_to_card(i)) == i

    def test_known_values(self) -> None:
        assert card_to_index("2c") == 0
        assert card_to_index("As") == 51

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            card_to_index("XY")
        with pytest.raises(ValueError):
            card_to_index("")
        with pytest.raises(ValueError):
            index_to_card(52)
        with pytest.raises(ValueError):
            index_to_card(-1)


# ── card_to_pt ────────────────────────────────────────────────────


class TestCardToPt:
    def test_known(self) -> None:
        assert card_to_pt("Ah") == "Ás de Copas"
        assert card_to_pt("2c") == "Dois de Paus"
        assert card_to_pt("Kd") == "Rei de Ouros"

    def test_invalid_returns_none(self) -> None:
        assert card_to_pt("XY") is None
        assert card_to_pt("") is None
        assert card_to_pt(None) is None  # type: ignore[arg-type]

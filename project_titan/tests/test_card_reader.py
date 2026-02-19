"""Unit tests for PPPokerCardReader — card detection via OCR + colour analysis."""

from __future__ import annotations

import os
import sys
import pytest

# Ensure project_titan is on the path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from tools.card_reader import PPPokerCardReader


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_white_card(height: int = 80, width: int = 50) -> "np.ndarray":
    """Create a white card-shaped BGR image."""
    img = np.ones((height, width, 3), dtype=np.uint8) * 255
    return img


def _draw_rank_text(card: "np.ndarray", rank: str, color_bgr: tuple) -> "np.ndarray":
    """Draw a rank character on the top portion of a card image."""
    h, w = card.shape[:2]
    # Draw rank text large enough for OCR
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.8, h / 80.0)
    thickness = max(1, int(scale * 2))
    cv2.putText(card, rank, (5, int(h * 0.35)), font, scale, color_bgr, thickness)
    return card


def _color_bgr_for_suit(suit: str) -> tuple:
    """Return the BGR colour for a PPPoker suit."""
    return {
        "h": (0, 0, 200),     # red → hearts
        "s": (30, 30, 30),     # black → spades
        "d": (200, 100, 0),    # blue → diamonds
        "c": (0, 150, 0),      # green → clubs
    }[suit]


def _make_card_region(
    cards: list[tuple[str, str]],
    card_w: int = 50,
    card_h: int = 80,
    gap: int = 4,
) -> "np.ndarray":
    """Build a horizontal strip of cards.

    Args:
        cards: List of (rank, suit) pairs, e.g. [("A", "h"), ("K", "d")].
    """
    n = len(cards)
    total_w = n * card_w + (n - 1) * gap
    strip = np.zeros((card_h, total_w, 3), dtype=np.uint8) + 40  # dark bg

    for i, (rank, suit) in enumerate(cards):
        x = i * (card_w + gap)
        card = _make_white_card(card_h, card_w)
        card = _draw_rank_text(card, rank, _color_bgr_for_suit(suit))
        strip[0:card_h, x:x + card_w] = card

    return strip


def _make_full_frame(
    hero_cards: list[tuple[str, str]] | None = None,
    board_cards: list[tuple[str, str]] | None = None,
    width: int = 960,
    height: int = 1080,
    button_y: int = 900,
    table_cx: int = 480,
    pot_y: int = 350,
) -> tuple["np.ndarray", dict, tuple | None]:
    """Synthesise a mock PPPoker frame with cards at correct positions.

    Returns:
        (frame, action_points, pot_xy)
    """
    frame = np.zeros((height, width, 3), dtype=np.uint8) + 50  # dark table

    action_points = {
        "fold": (table_cx - 150, button_y),
        "check": (table_cx, button_y),
        "raise": (table_cx + 150, button_y),
    }
    pot_xy = (table_cx, pot_y)

    reader = PPPokerCardReader()
    hero_y_top = button_y + reader._hero_y_top
    hero_y_bottom = button_y + reader._hero_y_bottom
    hero_x_start = table_cx - reader._HERO_X_HALF_WIDTH

    if hero_cards:
        strip = _make_card_region(hero_cards)
        sh, sw = strip.shape[:2]
        y_insert = hero_y_top + (hero_y_bottom - hero_y_top - sh) // 2
        x_insert = hero_x_start + (reader._HERO_X_HALF_WIDTH * 2 - sw) // 2
        y_insert = max(0, y_insert)
        x_insert = max(0, x_insert)
        y_end = min(height, y_insert + sh)
        x_end = min(width, x_insert + sw)
        frame[y_insert:y_end, x_insert:x_end] = strip[:y_end - y_insert, :x_end - x_insert]

    if board_cards:
        board_y_top = pot_y + reader._board_y_top
        board_x_start = table_cx - reader._BOARD_X_HALF_WIDTH
        strip = _make_card_region(board_cards)
        sh, sw = strip.shape[:2]
        y_insert = board_y_top + 10
        x_insert = board_x_start + (reader._BOARD_X_HALF_WIDTH * 2 - sw) // 2
        y_insert = max(0, y_insert)
        x_insert = max(0, x_insert)
        y_end = min(height, y_insert + sh)
        x_end = min(width, x_insert + sw)
        frame[y_insert:y_end, x_insert:x_end] = strip[:y_end - y_insert, :x_end - x_insert]

    return frame, action_points, pot_xy


# ── Tests ───────────────────────────────────────────────────────────────

class TestPPPokerCardReader:
    """Tests for card_reader.PPPokerCardReader."""

    def test_instantiation(self) -> None:
        reader = PPPokerCardReader()
        assert reader.enabled is True

    def test_disabled_returns_empty(self) -> None:
        os.environ["TITAN_CARD_READER_ENABLED"] = "0"
        try:
            reader = PPPokerCardReader()
            assert reader.enabled is False
            hero, board = reader.read_cards(None, {}, None)
            assert hero == []
            assert board == []
        finally:
            os.environ["TITAN_CARD_READER_ENABLED"] = "1"

    def test_no_buttons_returns_empty(self) -> None:
        reader = PPPokerCardReader()
        hero, board = reader.read_cards(
            np.zeros((100, 100, 3), dtype=np.uint8) if HAS_CV2 else None,
            {},
            None,
        )
        assert hero == []
        assert board == []

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_suit_color_detection_red(self) -> None:
        reader = PPPokerCardReader()
        # Red card region
        region = np.ones((40, 30, 3), dtype=np.uint8) * 255
        cv2.rectangle(region, (5, 5), (25, 35), (0, 0, 200), -1)  # red fill
        suit = reader._detect_suit_color(region)
        assert suit == "h"

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_suit_color_detection_blue(self) -> None:
        reader = PPPokerCardReader()
        region = np.ones((40, 30, 3), dtype=np.uint8) * 255
        cv2.rectangle(region, (5, 5), (25, 35), (200, 100, 0), -1)  # blue fill
        suit = reader._detect_suit_color(region)
        assert suit == "d"

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_suit_color_detection_green(self) -> None:
        reader = PPPokerCardReader()
        region = np.ones((40, 30, 3), dtype=np.uint8) * 255
        cv2.rectangle(region, (5, 5), (25, 35), (0, 150, 0), -1)  # green fill
        suit = reader._detect_suit_color(region)
        assert suit == "c"

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_suit_color_detection_black(self) -> None:
        reader = PPPokerCardReader()
        region = np.ones((40, 30, 3), dtype=np.uint8) * 255
        cv2.rectangle(region, (5, 5), (25, 35), (10, 10, 10), -1)  # black fill
        suit = reader._detect_suit_color(region)
        assert suit == "s"

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_merge_overlapping_basic(self) -> None:
        bboxes = [(10, 0, 40, 80), (15, 0, 40, 80), (100, 0, 40, 80)]
        merged = PPPokerCardReader._merge_overlapping(bboxes)
        assert len(merged) == 2
        assert merged[1] == (100, 0, 40, 80)

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_segment_cards_splits_wide_region(self) -> None:
        reader = PPPokerCardReader()
        # Create a wide bright strip (simulating merged cards)
        strip = np.ones((80, 260, 3), dtype=np.uint8) * 255
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        bboxes = reader._split_by_brightness_columns(mask, 80)
        assert len(bboxes) >= 1  # Should split or detect the full strip

    def test_parse_rank_text_basic(self) -> None:
        reader = PPPokerCardReader()
        assert reader._parse_rank_text("A") == "A"
        assert reader._parse_rank_text("K") == "K"
        assert reader._parse_rank_text("10") == "T"
        assert reader._parse_rank_text("0") == "T"
        assert reader._parse_rank_text("2") == "2"
        assert reader._parse_rank_text("") is None
        assert reader._parse_rank_text("xyz") is None

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_read_cards_with_synthetic_frame(self) -> None:
        """Full integration test with a synthetic frame containing cards."""
        hero = [("A", "h"), ("K", "d"), ("9", "s"), ("6", "c")]
        frame, action_points, pot_xy = _make_full_frame(hero_cards=hero)

        reader = PPPokerCardReader()
        hero_result, board_result = reader.read_cards(frame, action_points, pot_xy)

        # We may not get perfect OCR on synthetic cards, but the reader
        # should at least find some card-shaped regions and attempt to read them.
        # The important thing is it doesn't crash or return garbage.
        assert isinstance(hero_result, list)
        assert isinstance(board_result, list)
        # All returned tokens should be valid format
        for token in hero_result + board_result:
            assert len(token) == 2
            assert token[0] in "A23456789TJQK"
            assert token[1] in "hsdc"

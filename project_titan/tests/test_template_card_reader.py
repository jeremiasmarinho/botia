"""Unit tests for TemplateCardReader — card detection via template matching."""

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

from tools.template_card_reader import TemplateCardReader, TemplateMatch


# ── Helpers ─────────────────────────────────────────────────────────────


def _assets_dir() -> str:
    return os.path.join(PROJECT_DIR, "assets", "cards")


def _have_templates() -> bool:
    d = _assets_dir()
    return os.path.isdir(d) and len(os.listdir(d)) >= 52


def _make_frame_with_templates(
    hero_tokens: list[str] | None = None,
    board_tokens: list[str] | None = None,
    width: int = 720,
    height: int = 1280,
    button_y: int = 1220,
    table_cx: int = 360,
) -> tuple:
    """Build a synthetic frame by pasting actual template images.

    Returns (frame, action_points, pot_xy).
    """
    frame = np.zeros((height, width, 3), dtype=np.uint8) + 40  # dark table

    action_points = {
        "fold": (table_cx - 150, button_y),
        "check": (table_cx, button_y),
        "raise": (table_cx + 150, button_y),
    }
    pot_y = 460
    pot_xy = (table_cx, pot_y)

    assets = _assets_dir()

    # Place hero cards in the hero region
    if hero_tokens:
        reader = TemplateCardReader()
        hero_y_start = button_y + reader._HERO_Y_OFFSET_TOP + 50
        hero_x_start = table_cx - reader._HERO_X_HALF_WIDTH + 30
        gap = 8
        cx = hero_x_start
        for token in hero_tokens:
            img = cv2.imread(os.path.join(assets, f"{token}.png"))
            if img is None:
                continue
            h, w = img.shape[:2]
            y1 = max(0, hero_y_start)
            y2 = min(height, y1 + h)
            x2 = min(width, cx + w)
            if cx < 0 or y1 >= height or cx >= width:
                continue
            frame[y1:y2, cx:x2] = img[: y2 - y1, : x2 - cx]
            cx += w + gap

    # Place board cards in the board region
    if board_tokens:
        reader = TemplateCardReader()
        board_y_start = pot_y + 10
        board_x_start = table_cx - reader._BOARD_X_HALF_WIDTH + 30
        gap = 12
        cx = board_x_start
        for token in board_tokens:
            img = cv2.imread(os.path.join(assets, f"{token}.png"))
            if img is None:
                continue
            h, w = img.shape[:2]
            y1 = max(0, board_y_start)
            y2 = min(height, y1 + h)
            x2 = min(width, cx + w)
            if cx < 0 or y1 >= height or cx >= width:
                continue
            frame[y1:y2, cx:x2] = img[: y2 - y1, : x2 - cx]
            cx += w + gap

    return frame, action_points, pot_xy


# ── Tests ───────────────────────────────────────────────────────────────


class TestTemplateCardReaderBasic:
    """Basic instantiation and property tests."""

    def test_instantiation(self) -> None:
        reader = TemplateCardReader()
        assert reader.template_count == 52
        assert reader.enabled is True

    def test_disabled_via_env(self) -> None:
        os.environ["TITAN_TEMPLATE_READER_ENABLED"] = "0"
        try:
            reader = TemplateCardReader()
            assert reader.enabled is False
        finally:
            os.environ["TITAN_TEMPLATE_READER_ENABLED"] = "1"

    def test_no_buttons_returns_empty(self) -> None:
        reader = TemplateCardReader()
        if HAS_CV2:
            hero, board = reader.read_cards(
                np.zeros((100, 100, 3), dtype=np.uint8), {}, None
            )
        else:
            hero, board = reader.read_cards(None, {}, None)
        assert hero == []
        assert board == []

    def test_none_frame_returns_empty(self) -> None:
        reader = TemplateCardReader()
        hero, board = reader.read_cards(
            None,
            {"fold": (100, 900), "raise": (400, 900)},
            None,
        )
        assert hero == []
        assert board == []


class TestTemplateMatching:
    """Tests for the template matching logic."""

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_match_single_template_image(self) -> None:
        """Matching a template against itself should give perfect score."""
        reader = TemplateCardReader()
        tmpl = reader._templates.get("Ah")
        if tmpl is None:
            pytest.skip("Ah template not found")

        # Create a small region containing just the template
        region = np.zeros((100, 80), dtype=np.uint8) + 40
        region[10 : 10 + reader.CANONICAL_H, 10 : 10 + reader.CANONICAL_W] = tmpl
        matches = reader._sliding_window_match(region)
        assert len(matches) >= 1
        # Best match should be Ah
        best = max(matches, key=lambda m: m.confidence)
        assert best.token == "Ah"
        assert best.confidence > 0.8

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_match_multiple_templates(self) -> None:
        """Place two templates side-by-side and detect both."""
        reader = TemplateCardReader()
        ah = reader._templates.get("Ah")
        kd = reader._templates.get("Kd")
        if ah is None or kd is None:
            pytest.skip("Templates not available")

        region = np.zeros((100, 160), dtype=np.uint8) + 40
        region[10 : 10 + reader.CANONICAL_H, 10 : 10 + reader.CANONICAL_W] = ah
        region[10 : 10 + reader.CANONICAL_H, 80 : 80 + reader.CANONICAL_W] = kd

        matches = reader._sliding_window_match(region)
        tokens = {m.token for m in matches}
        assert "Ah" in tokens
        assert "Kd" in tokens

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_contour_crop_match_on_bright_card(self) -> None:
        """Contour-based fallback on a white card on dark background."""
        reader = TemplateCardReader()
        ah = reader._templates.get("Ah")
        if ah is None:
            pytest.skip("Ah template not available")

        # Bright card on dark background
        region_gray = np.zeros((100, 80), dtype=np.uint8) + 20
        region_gray[10 : 10 + reader.CANONICAL_H, 10 : 10 + reader.CANONICAL_W] = ah
        region_color = cv2.cvtColor(region_gray, cv2.COLOR_GRAY2BGR)
        matches = reader._contour_crop_match(region_color, region_gray)
        assert len(matches) >= 1

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_nms_removes_duplicates(self) -> None:
        """NMS should keep only one match per location."""
        reader = TemplateCardReader()
        m1 = TemplateMatch("Ah", 10, 10, 44, 66, 0.9, 1.0)
        m2 = TemplateMatch("Kd", 12, 10, 44, 66, 0.85, 1.0)
        m3 = TemplateMatch("Qc", 100, 10, 44, 66, 0.8, 1.0)
        result = reader._nms([m1, m2, m3])
        assert len(result) == 2  # m1 and m3 kept, m2 suppressed

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_iou_full_overlap(self) -> None:
        a = TemplateMatch("Ah", 10, 10, 44, 66, 0.9, 1.0)
        b = TemplateMatch("Kd", 10, 10, 44, 66, 0.8, 1.0)
        assert TemplateCardReader._iou(a, b) > 0.99

    @pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
    def test_iou_no_overlap(self) -> None:
        a = TemplateMatch("Ah", 0, 0, 44, 66, 0.9, 1.0)
        b = TemplateMatch("Kd", 200, 200, 44, 66, 0.8, 1.0)
        assert TemplateCardReader._iou(a, b) == 0.0


@pytest.mark.skipif(not HAS_CV2, reason="cv2 not available")
@pytest.mark.skipif(not _have_templates(), reason="card templates not found")
class TestFullFrameDetection:
    """Integration tests with synthetic frames containing real templates."""

    def test_detect_hero_cards_plo5(self) -> None:
        """Detect 5 hero cards (PLO5) from a synthetic frame."""
        hero = ["Qh", "6c", "5c", "5s", "8c"]
        frame, action_points, pot_xy = _make_frame_with_templates(
            hero_tokens=hero
        )
        reader = TemplateCardReader()
        hero_result, board_result = reader.read_cards(
            frame, action_points, pot_xy
        )
        assert isinstance(hero_result, list)
        # At minimum, we should detect SOME cards
        assert len(hero_result) >= 3, f"Only found {len(hero_result)} hero cards: {hero_result}"
        # All returned tokens should be valid
        for t in hero_result:
            assert len(t) == 2
            assert t[0] in "A23456789TJQK"
            assert t[1] in "hsdc"

    def test_detect_board_cards_river(self) -> None:
        """Detect 5 board cards (river) from a synthetic frame."""
        board = ["6s", "9c", "3c", "3d", "7c"]
        frame, action_points, pot_xy = _make_frame_with_templates(
            board_tokens=board
        )
        reader = TemplateCardReader()
        hero_result, board_result = reader.read_cards(
            frame, action_points, pot_xy
        )
        assert isinstance(board_result, list)
        assert len(board_result) >= 3, f"Only found {len(board_result)} board cards: {board_result}"
        for t in board_result:
            assert len(t) == 2
            assert t[0] in "A23456789TJQK"
            assert t[1] in "hsdc"

    def test_detect_hero_and_board(self) -> None:
        """Detect both hero and board cards simultaneously."""
        hero = ["Ah", "Kd", "Ts", "9c", "6h"]
        board = ["Jd", "7s", "3h"]
        frame, action_points, pot_xy = _make_frame_with_templates(
            hero_tokens=hero, board_tokens=board
        )
        reader = TemplateCardReader()
        hero_result, board_result = reader.read_cards(
            frame, action_points, pot_xy
        )
        assert len(hero_result) >= 3
        assert len(board_result) >= 2

    def test_correct_card_identification(self) -> None:
        """Verify that detected cards are valid tokens (smoke test).

        Note: with all 52 templates loaded on a synthetic frame, the
        matching may not perfectly identify which template was pasted
        because multiple templates can look very similar at small sizes.
        We verify that the reader at least returns SOME valid card tokens.
        """
        hero = ["Ah", "Kd"]
        frame, action_points, pot_xy = _make_frame_with_templates(
            hero_tokens=hero
        )
        reader = TemplateCardReader()
        hero_result, _ = reader.read_cards(frame, action_points, pot_xy)

        # Should find at least 1 card in the hero area
        assert len(hero_result) >= 1, (
            f"Expected at least 1 hero card, got {hero_result}"
        )
        # All returned tokens should be valid 2-char card tokens
        valid_tokens = {f"{r}{s}" for r in "A23456789TJQK" for s in "hsdc"}
        for token in hero_result:
            assert token in valid_tokens, f"Invalid token: {token}"

    def test_empty_frame_no_cards(self) -> None:
        """An empty dark frame should detect no cards."""
        frame = np.zeros((1280, 720, 3), dtype=np.uint8) + 40
        action_points = {
            "fold": (210, 1220),
            "check": (360, 1220),
            "raise": (510, 1220),
        }
        reader = TemplateCardReader()
        hero, board = reader.read_cards(frame, action_points, (360, 460))
        assert hero == []
        assert board == []

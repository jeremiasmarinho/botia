"""PPPoker card reader — OCR + color analysis for card detection.

Provides an alternative card detection pipeline for when YOLO fails to
detect card classes but successfully detects UI elements (buttons, pot, stack).

Strategy
--------
1. **Region estimation** — uses detected button positions (from YOLO) as
   anchors to calculate where hero and board cards should be located.
2. **Card isolation** — finds individual card faces via brightness
   thresholding and contour detection on the estimated regions.
3. **Rank reading** — Tesseract OCR on the rank area of each detected card.
4. **Suit detection** — HSV color analysis of the rank/suit area:
   red → ♥ (hearts), black → ♠ (spades), blue → ♦ (diamonds),
   green → ♣ (clubs).

PPPoker card visual characteristics
------------------------------------
- White/cream background with rounded corners.
- Gold border on hero cards.
- Large rank character (top-left corner) and suit symbol below.
- Suit colours:  ♥ = red  |  ♠ = black  |  ♦ = blue  |  ♣ = green.

Environment variables
---------------------
``TITAN_CARD_READER_ENABLED``       Set to ``1`` to enable (default: ``1``).
``TITAN_CARD_READER_DEBUG``         Set to ``1`` to save debug crops.
``TITAN_CARD_READER_DEBUG_DIR``     Directory for debug images.
``TITAN_CARD_READER_HERO_OFFSET_Y`` Override hero-region Y offset from buttons.
``TITAN_CARD_READER_BOARD_OFFSET_Y`` Override board-region Y offset from pot.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Lazy imports — avoid hard dependency on cv2 / numpy / pytesseract
# ---------------------------------------------------------------------------

_cv2: Any | None = None
_np: Any | None = None
_pytesseract: Any | None = None


def _ensure_deps() -> bool:
    """Lazy-import cv2, numpy, pytesseract.  Returns True on success."""
    global _cv2, _np, _pytesseract  # noqa: PLW0603
    if _cv2 is not None and _np is not None:
        return True
    try:
        import cv2  # type: ignore[import-untyped]
        _cv2 = cv2
    except ImportError:
        return False
    try:
        import numpy as np
        _np = np
    except ImportError:
        return False
    try:
        import pytesseract as tess  # type: ignore[import-untyped]
        cmd = os.getenv("TITAN_TESSERACT_CMD", "").strip()
        if not cmd:
            # Auto-detect common Tesseract install paths on Windows
            _candidates = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
            ]
            for _cand in _candidates:
                if os.path.isfile(_cand):
                    cmd = _cand
                    break
        if cmd:
            tess.pytesseract.tesseract_cmd = cmd
        _pytesseract = tess
    except ImportError:
        _pytesseract = None  # OCR will use fallback
    return True


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CardCandidate:
    """A single detected card with its position and reading."""
    rank: str           # e.g. "A", "K", "T", "9"
    suit: str           # "h", "s", "d", "c"
    token: str          # e.g. "Ah", "Td"
    cx: int             # center-x in frame coordinates
    cy: int             # center-y in frame coordinates
    w: int              # bounding-box width
    h: int              # bounding-box height
    confidence: float   # pseudo-confidence [0-1]


# ---------------------------------------------------------------------------
# PPPokerCardReader
# ---------------------------------------------------------------------------

class PPPokerCardReader:
    """Reads PPPoker cards via contour detection + OCR + colour analysis.

    Typical usage::

        reader = PPPokerCardReader()
        hero, board = reader.read_cards(frame, action_points, pot_xy)
        # hero  = ["Ah", "Kd", "Ts", "9c", "6h", "2s"]
        # board = ["Jd", "7s", "3h"]
    """

    # -- Rank OCR mapping --------------------------------------------------
    # Maps raw OCR output → canonical rank character.
    _RANK_MAP: dict[str, str] = {
        "A": "A", "a": "A",
        "K": "K", "k": "K",
        "Q": "Q", "q": "Q",
        "J": "J", "j": "J",
        "T": "T", "t": "T",
        "10": "T",
        "9": "9", "8": "8", "7": "7", "6": "6",
        "5": "5", "4": "4", "3": "3", "2": "2",
        "1": "A",   # OCR quirk: sometimes reads Ace as 1
        "0": "T",   # OCR quirk: sometimes reads 10 as 0
    }

    # Valid canonical ranks for final validation
    _VALID_RANKS = set("A23456789TJQK")
    _VALID_SUITS = set("hsdc")

    # -- Hero card region geometry (relative to button centre) -------------
    # These are the default pixel offsets.  Button Y is the average Y of
    # the detected fold / call / raise buttons.
    #
    # From empirical PPPoker analysis on 720×1280 (LDPlayer portrait):
    #   hero cards ≈ button_y − 420  to  button_y − 260
    #   hero cards width ≈ 460px centred on midpoint of fold↔raise
    _HERO_Y_OFFSET_TOP: int = -420      # above button_y
    _HERO_Y_OFFSET_BOTTOM: int = -150   # above button_y (PLO5 hero cards may be lower)
    _HERO_X_HALF_WIDTH: int = 310       # ± from table centre (PLO5 hero cards fan wider)

    # -- Board card region geometry (relative to pot / estimated position) --
    # Board cards sit just below the pot indicator in PPPoker.
    # Widened to avoid clipping card rank labels at the edges.
    _BOARD_Y_OFFSET_TOP: int = -40      # above pot_y (generous margin)
    _BOARD_Y_OFFSET_BOTTOM: int = 200   # below pot_y
    _BOARD_X_HALF_WIDTH: int = 260      # ± from pot centre
    # Fallback: if pot is not detected, estimate board position relative
    # to buttons.  Board is approximately 700-800 px above the buttons.
    _BOARD_FALLBACK_Y_OFFSET: int = -760  # above button_y

    # -- Card segmentation thresholds --------------------------------------
    # PPPoker cards have gold/cream backgrounds; a threshold of 200 is
    # too aggressive and misses most card contours.  Lowered to 140.
    _BRIGHT_THRESHOLD: int = 140        # grayscale value to consider "bright"
    _MIN_CARD_WIDTH: int = 30           # minimum contour width for a card
    _MAX_CARD_WIDTH: int = 120          # maximum contour width
    _MIN_CARD_HEIGHT: int = 30           # minimum contour height (lowered for MuMu auto-crop)
    _MAX_CARD_HEIGHT: int = 150         # maximum contour height
    _MIN_CARD_ASPECT: float = 0.3       # min width/height ratio
    _MAX_CARD_ASPECT: float = 1.2       # max width/height ratio (allow near-square cards too)

    def __init__(self) -> None:
        self._debug = os.getenv("TITAN_CARD_READER_DEBUG", "0").strip() == "1"
        self._debug_dir = os.getenv(
            "TITAN_CARD_READER_DEBUG_DIR",
            os.path.join("reports", "debug_cards"),
        ).strip()
        self._enabled = os.getenv(
            "TITAN_CARD_READER_ENABLED", "1"
        ).strip() in {"1", "true", "yes", "on"}

        # Auto-template learning: save OCR-identified card crops as
        # MuMu-native templates for the TemplateCardReader.
        self._auto_template_dir = os.path.join("assets", "cards_mumu")
        self._auto_template_enabled = os.getenv(
            "TITAN_AUTO_TEMPLATE_LEARNING", "1"
        ).strip() in {"1", "true", "yes", "on"}

        # Allow env-var overrides for region offsets
        self._hero_y_top = self._env_int(
            "TITAN_CARD_READER_HERO_OFFSET_Y_TOP", self._HERO_Y_OFFSET_TOP
        )
        self._hero_y_bottom = self._env_int(
            "TITAN_CARD_READER_HERO_OFFSET_Y_BOTTOM", self._HERO_Y_OFFSET_BOTTOM
        )
        self._board_y_top = self._env_int(
            "TITAN_CARD_READER_BOARD_OFFSET_Y_TOP", self._BOARD_Y_OFFSET_TOP
        )
        self._board_y_bottom = self._env_int(
            "TITAN_CARD_READER_BOARD_OFFSET_Y_BOTTOM", self._BOARD_Y_OFFSET_BOTTOM
        )

    # ── Public API ─────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def read_cards(
        self,
        frame: Any,
        action_points: dict[str, tuple[int, int]],
        pot_xy: tuple[int, int] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Read hero and board cards from a captured frame.

        Args:
            frame:         BGR numpy array (full screen or ROI capture).
            action_points: Dict ``{action_name: (x, y)}`` from YOLO detection.
            pot_xy:        ``(x, y)`` centre of the pot indicator, or ``None``.

        Returns:
            ``(hero_cards, board_cards)`` — lists of card tokens like ``"Ah"``.
        """
        if not self._enabled or not _ensure_deps():
            return [], []
        if frame is None or _np is None:
            return [], []

        np = _np
        cv2 = _cv2
        h_frame, w_frame = frame.shape[:2]

        # -- Filter buttons to a single table (spatial clustering) ---------
        # On dual-table setups YOLO may return buttons from both tables.
        # Keep only buttons that are spatially consistent (same Y band
        # and same X half of the screen).
        filtered = self._cluster_buttons(action_points)

        button_xs: list[int] = []
        button_ys: list[int] = []
        for name in ("fold", "call", "check", "raise"):
            if name in filtered:
                bx, by = filtered[name]
                button_xs.append(int(bx))
                button_ys.append(int(by))

        if not button_xs:
            return [], []

        table_center_x = int(sum(button_xs) / len(button_xs))
        button_y = int(sum(button_ys) / len(button_ys))

        # -- Hero card region ------------------------------------------------
        hero_y1 = max(0, button_y + self._hero_y_top)
        hero_y2 = min(h_frame, button_y + self._hero_y_bottom)
        hero_x1 = max(0, table_center_x - self._HERO_X_HALF_WIDTH)
        hero_x2 = min(w_frame, table_center_x + self._HERO_X_HALF_WIDTH)

        hero_cards: list[str] = []
        if hero_y2 > hero_y1 and hero_x2 > hero_x1:
            hero_crop = frame[hero_y1:hero_y2, hero_x1:hero_x2]
            hero_cards = self._read_cards_in_region(
                hero_crop, "hero", hero_x1, hero_y1
            )

        # -- Board card region -----------------------------------------------
        board_cards: list[str] = []
        # Determine board center: prefer pot indicator, else fallback to
        # estimating from button positions.
        if pot_xy is not None:
            board_cx, board_cy = int(pot_xy[0]), int(pot_xy[1])
        else:
            # Fallback: board is ~580px above buttons, centred on table
            board_cx = table_center_x
            board_cy = max(0, button_y + self._BOARD_FALLBACK_Y_OFFSET)

        board_y1 = max(0, board_cy + self._board_y_top)
        board_y2 = min(h_frame, board_cy + self._board_y_bottom)
        board_x1 = max(0, board_cx - self._BOARD_X_HALF_WIDTH)
        board_x2 = min(w_frame, board_cx + self._BOARD_X_HALF_WIDTH)

        if board_y2 > board_y1 and board_x2 > board_x1:
            board_crop = frame[board_y1:board_y2, board_x1:board_x2]
            board_cards = self._read_cards_in_region(
                board_crop, "board", board_x1, board_y1
            )

        if self._debug:
            self._save_debug(
                frame, hero_cards, board_cards,
                (hero_x1, hero_y1, hero_x2, hero_y2),
                (board_x1, board_y1, board_x2, board_y2),
            )

        return hero_cards, board_cards

    # ── Table clustering ───────────────────────────────────────────

    @staticmethod
    def _cluster_buttons(
        action_points: dict[str, tuple[int, int]],
    ) -> dict[str, tuple[int, int]]:
        """Filter action_points to keep only buttons from a single table.

        On dual-table setups, YOLO may detect buttons from both tables.
        This groups buttons by Y proximity (within 80px) and X proximity
        (within 500px), then picks the largest consistent group.
        If fold+raise are both present, uses their midpoint as the
        table anchor.
        """
        primary_names = {"fold", "call", "check", "raise"}
        primaries = [
            (name, int(xy[0]), int(xy[1]))
            for name, xy in action_points.items()
            if name in primary_names
        ]

        if len(primaries) <= 1:
            return action_points  # nothing to cluster

        # Simple clustering: pick the Y value most buttons agree on
        # (within 80px tolerance), then filter X outliers.
        y_values = [y for _, _, y in primaries]
        best_y = max(set(y_values), key=lambda yv: sum(1 for y2 in y_values if abs(y2 - yv) < 80))

        # Keep buttons within 80px of the dominant Y
        y_ok = [(n, x, y) for n, x, y in primaries if abs(y - best_y) < 80]

        if len(y_ok) >= 2:
            # Also filter X outliers: keep within 500px of the median X
            x_vals = sorted(x for _, x, _ in y_ok)
            median_x = x_vals[len(x_vals) // 2]
            y_ok = [(n, x, y) for n, x, y in y_ok if abs(x - median_x) < 500]

        if not y_ok:
            return action_points  # fallback: keep everything

        filtered = {n: (x, y) for n, x, y in y_ok}

        # Carry over non-button keys (pot_indicator, stack_indicator)
        # but only if they are on the same side of the screen.
        if filtered:
            fx_vals = [x for _, (x, _) in filtered.items()]
            f_center_x = sum(fx_vals) // len(fx_vals)
            for key, xy in action_points.items():
                if key in primary_names:
                    continue
                # Keep if within 400px of the filtered button centre
                if abs(int(xy[0]) - f_center_x) < 400:
                    filtered[key] = xy

        return filtered

    # ── Card region processing ─────────────────────────────────────

    def _read_cards_in_region(
        self,
        region: Any,
        zone: str,
        offset_x: int,
        offset_y: int,
    ) -> list[str]:
        """Find and read individual cards within a cropped region.

        Uses brightness thresholding to find white rectangular card faces,
        then reads rank via OCR and suit via colour analysis.

        Robust to emulators where cards are dim (MuMu Vulkan) or occupy
        only a small vertical slice of the region — auto-crops to the
        bright area before contour detection, retries with progressively
        lower thresholds, and splits wide merged contours.
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return []

        h_r, w_r = region.shape[:2]
        if h_r < 20 or w_r < 20:
            return []

        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

        # ── Auto-crop to bright Y band ──────────────────────────────
        # When cards occupy only a small vertical slice (e.g. bottom 60px
        # of a 270px hero crop), column brightness ratios get diluted and
        # the fallback splitter fails.  Detect the Y range where most
        # bright pixels live and crop to it (with 10px margin).
        #
        # Use threshold=160 for AUTO-CROP: this is ONLY to identify the
        # vertical band where card faces sit.  A high threshold avoids
        # including dim felt/background rows.  The actual card detection
        # (contour + column-split) uses lower thresholds below.
        working_region = region
        working_gray = gray
        crop_y_offset = 0
        _, bright_full = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
        row_brightness = np.mean(bright_full > 0, axis=1)  # fraction per row
        bright_rows = np.where(row_brightness > 0.05)[0]
        if len(bright_rows) > 0:
            y_top = max(0, int(bright_rows[0]) - 10)
            y_bot = min(h_r, int(bright_rows[-1]) + 10)
            if (y_bot - y_top) < h_r * 0.85:  # only crop if it saves >15%
                working_region = region[y_top:y_bot, :]
                working_gray = gray[y_top:y_bot, :]
                crop_y_offset = y_top

        h_w, w_w = working_region.shape[:2]
        if h_w < 10 or w_w < 10:
            return []

        # ── Multi-threshold card detection ──────────────────────────
        # Try progressively lower thresholds and keep the result that
        # finds the MOST individual cards.  MuMu Vulkan renders cards
        # dimmer on the left side (brightness ~120-160), so higher
        # thresholds only catch the right-side cards.
        #
        # Strategy: COMBINE detections from multiple thresholds.
        # Start from the highest threshold (cleanest), then add NEW cards
        # from lower thresholds that don't overlap with already-found cards.
        # This catches both bright and dim cards in a single pass.

        thresholds = [self._BRIGHT_THRESHOLD, 120, 100, 80]
        combined_bboxes: list[tuple[int, int, int, int]] = []

        for thresh in thresholds:
            _, bright_mask = cv2.threshold(
                working_gray, thresh, 255, cv2.THRESH_BINARY
            )
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_CLOSE, kernel)

            contours, _ = cv2.findContours(
                bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            new_bboxes: list[tuple[int, int, int, int]] = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if w < self._MIN_CARD_WIDTH:
                    continue
                if h < self._MIN_CARD_HEIGHT or h > self._MAX_CARD_HEIGHT:
                    continue
                aspect = w / max(h, 1)
                if aspect < self._MIN_CARD_ASPECT:
                    continue

                # If contour is wider than a single card, split it
                if w > self._MAX_CARD_WIDTH * 1.3:
                    n_cards = max(2, round(w / 55))  # ~55px per card
                    card_w = w // n_cards
                    for i in range(n_cards):
                        cx = x + i * card_w
                        cw = card_w if i < n_cards - 1 else (x + w - cx)
                        new_bboxes.append((cx, y, cw, h))
                elif aspect <= self._MAX_CARD_ASPECT:
                    new_bboxes.append((x, y, w, h))

            if not new_bboxes:
                # Fallback: split by brightness columns
                new_bboxes = self._split_by_brightness_columns(
                    bright_mask, h_w
                )

            # Add only bboxes that don't overlap with already-found cards
            for bbox in new_bboxes:
                if not self._overlaps_existing(bbox, combined_bboxes):
                    combined_bboxes.append(bbox)

            # Zone-aware limits: hero max 7, board max 6 (gives OCR
            # room to filter, final output capped at 5 below).
            max_bboxes = 7 if zone == "hero" else 6
            if len(combined_bboxes) >= max_bboxes:
                break

        card_bboxes = combined_bboxes

        if not card_bboxes:
            return []

        # Sort by x position (left to right)
        card_bboxes.sort(key=lambda b: b[0])

        # Merge overlapping bboxes (cards that got split by contour noise)
        card_bboxes = self._merge_overlapping(card_bboxes)

        # Re-split any merged bboxes that are too wide after merging
        final_bboxes: list[tuple[int, int, int, int]] = []
        for x, y, w, h in card_bboxes:
            if w > self._MAX_CARD_WIDTH * 1.3:
                n_cards = max(2, round(w / 55))
                card_w = w // n_cards
                for i in range(n_cards):
                    cx = x + i * card_w
                    cw = card_w if i < n_cards - 1 else (x + w - cx)
                    final_bboxes.append((cx, y, cw, h))
            else:
                final_bboxes.append((x, y, w, h))
        card_bboxes = final_bboxes

        # Read each card — use working_region (auto-cropped)
        cards: list[str] = []
        seen_tokens: set[str] = set()
        for x, y, w, h in card_bboxes:
            card_crop = working_region[y:y + h, x:x + w]
            token = self._read_single_card(card_crop)
            if token and token not in seen_tokens:
                cards.append(token)
                seen_tokens.add(token)

        # Cap to max cards per zone (PLO5: 5 hero, 5 board)
        max_cards = 5
        if len(cards) > max_cards:
            cards = cards[:max_cards]

        if self._debug:
            # Adjust bboxes back to full-region coordinates for debug
            debug_bboxes = [(x, y + crop_y_offset, w, h) for x, y, w, h in card_bboxes]
            self._save_region_debug(region, debug_bboxes, cards, zone)

        return cards

    def _split_by_brightness_columns(
        self,
        bright_mask: Any,
        region_height: int,
    ) -> list[tuple[int, int, int, int]]:
        """Fallback card segmentation: detect bright column runs.

        Scans columns of the bright mask, finds contiguous runs of columns
        where a significant fraction of pixels are bright, and treats each
        run as a candidate card.
        """
        np = _np
        if np is None:
            return []

        h, w = bright_mask.shape[:2]
        # Column brightness profile: fraction of bright pixels per column
        col_brightness = np.mean(bright_mask > 0, axis=0)  # shape: (w,)

        # Threshold: column is "card" if ≥ 15% of its pixels are bright.
        # Lowered from 30% because some emulators (MuMu Vulkan) render
        # card faces dimmer and regions may be taller than the cards.
        is_card_col = col_brightness >= 0.15

        # Find contiguous runs of card columns
        segments: list[tuple[int, int]] = []
        in_segment = False
        seg_start = 0
        for col in range(w):
            if is_card_col[col] and not in_segment:
                seg_start = col
                in_segment = True
            elif not is_card_col[col] and in_segment:
                seg_width = col - seg_start
                if seg_width >= self._MIN_CARD_WIDTH:
                    segments.append((seg_start, col))
                in_segment = False
        if in_segment:
            seg_width = w - seg_start
            if seg_width >= self._MIN_CARD_WIDTH:
                segments.append((seg_start, w))

        # Convert segments to bboxes with PER-SEGMENT vertical bounds.
        # Instead of using full region_height, scan each column segment
        # to find where brightness is actually concentrated vertically.
        bboxes: list[tuple[int, int, int, int]] = []
        for x_start, x_end in segments:
            seg_w = x_end - x_start

            # Find vertical extent of brightness in this column segment
            seg_mask = bright_mask[:, x_start:x_end]
            row_bright = np.mean(seg_mask > 0, axis=1)
            bright_rows_seg = np.where(row_bright > 0.10)[0]
            if len(bright_rows_seg) > 0:
                y_top_seg = max(0, int(bright_rows_seg[0]) - 5)
                y_bot_seg = min(h, int(bright_rows_seg[-1]) + 5)
            else:
                y_top_seg = 0
                y_bot_seg = h
            seg_h = y_bot_seg - y_top_seg

            # Clamp height to MAX_CARD_HEIGHT if needed
            if seg_h > self._MAX_CARD_HEIGHT:
                mid = (y_top_seg + y_bot_seg) // 2
                y_top_seg = max(0, mid - self._MAX_CARD_HEIGHT // 2)
                y_bot_seg = min(h, y_top_seg + self._MAX_CARD_HEIGHT)
                seg_h = y_bot_seg - y_top_seg

            # Skip if too short
            if seg_h < self._MIN_CARD_HEIGHT:
                continue

            # If segment is very wide, it might be multiple cards merged.
            # Try to split it into ~card-width pieces.
            if seg_w > self._MAX_CARD_WIDTH * 1.5:
                n_cards = max(1, round(seg_w / 42))  # ~42px per card
                card_w = seg_w // n_cards
                for i in range(n_cards):
                    cx = x_start + i * card_w
                    cw = card_w if i < n_cards - 1 else (x_end - cx)
                    bboxes.append((cx, y_top_seg, cw, seg_h))
            else:
                bboxes.append((x_start, y_top_seg, seg_w, seg_h))

        return bboxes

    @staticmethod
    def _overlaps_existing(
        bbox: tuple[int, int, int, int],
        existing: list[tuple[int, int, int, int]],
        min_overlap_frac: float = 0.4,
    ) -> bool:
        """Check if *bbox* overlaps with any existing bbox by > threshold."""
        bx, by, bw, bh = bbox
        for ex, ey, ew, eh in existing:
            overlap_x = max(0, min(bx + bw, ex + ew) - max(bx, ex))
            if overlap_x > min(bw, ew) * min_overlap_frac:
                return True
        return False

    @staticmethod
    def _merge_overlapping(
        bboxes: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        """Merge bounding boxes that overlap horizontally by > 50%."""
        if len(bboxes) <= 1:
            return bboxes

        merged: list[tuple[int, int, int, int]] = [bboxes[0]]
        for x, y, w, h in bboxes[1:]:
            px, py, pw, ph = merged[-1]
            overlap_start = max(px, x)
            overlap_end = min(px + pw, x + w)
            overlap = max(0, overlap_end - overlap_start)
            if overlap > min(pw, w) * 0.5:
                # Merge: expand previous bbox
                new_x = min(px, x)
                new_y = min(py, y)
                new_x2 = max(px + pw, x + w)
                new_y2 = max(py + ph, y + h)
                merged[-1] = (new_x, new_y, new_x2 - new_x, new_y2 - new_y)
            else:
                merged.append((x, y, w, h))
        return merged

    # ── Single card reading ────────────────────────────────────────

    def _read_single_card(self, card_crop: Any) -> str | None:
        """Read rank + suit from a single card crop.

        The rank is read via OCR from the upper portion of the card.
        The suit is determined by colour analysis of the rank/symbol area.
        If the rank region fails, tries the full card crop as well.
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None or card_crop is None:
            return None

        h, w = card_crop.shape[:2]
        if h < 10 or w < 10:
            return None

        # ── Rank OCR: focus on JUST the rank text (top-left corner) ──
        # PPPoker shows a single rank char in the top ~35% of the card,
        # left ~50%.  Cropping tighter avoids reading the suit symbol
        # into the OCR string.
        rank_h = max(10, int(h * 0.38))
        rank_w = max(8, int(w * 0.55))
        rank_corner = card_crop[0:rank_h, 0:rank_w]

        # Try rank corner first, then wider region, then full card
        rank = self._ocr_rank(rank_corner)
        if not rank:
            rank_region = card_crop[0:max(10, int(h * 0.50)), :]
            rank = self._ocr_rank(rank_region)
        if not rank:
            rank = self._ocr_rank(card_crop)

        # ── Suit colour: use the FULL card for better colour sample ──
        # More pixels → more reliable hue histogram.
        suit = self._detect_suit_color(card_crop)
        if not suit:
            suit = self._detect_suit_color(rank_corner)

        if rank and suit:
            token = f"{rank}{suit}"
            # Auto-template learning: save this card crop as a MuMu template
            self._save_auto_template(card_crop, token)
            return token

        return None

    def _save_auto_template(self, card_crop: Any, token: str) -> None:
        """Save card crop as a MuMu-native template for future matching.

        Only saves if auto-template learning is enabled and this token
        doesn't already have a saved template.  Templates are stored in
        ``assets/cards_mumu/`` and can be loaded by TemplateCardReader.
        """
        if not self._auto_template_enabled:
            return
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return

        try:
            os.makedirs(self._auto_template_dir, exist_ok=True)
            dest = os.path.join(self._auto_template_dir, f"{token}.png")
            if os.path.exists(dest):
                return  # already have this template
            # Only save if the crop looks like a valid card face
            # (bright enough with some content variation)
            gray = cv2.cvtColor(card_crop, cv2.COLOR_BGR2GRAY)
            mean_val = float(np.mean(gray))
            if mean_val < 100 or mean_val > 250:
                return  # too dark or too white — probably not a card
            cv2.imwrite(dest, card_crop)
        except Exception:
            pass

    def _ocr_rank(self, rank_region: Any) -> str | None:
        """OCR the rank character from the card's top region.

        Multi-strategy pipeline for robustness across emulators:
        1. Upscale 5× for tiny MuMu/PPPoker card crops
        2. Try multiple thresholding approaches (OTSU, CLAHE, fixed)
        3. For each, try both polarities (normal + inverted)
        4. Take the first valid rank from Tesseract
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return None

        try:
            gray = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
        except Exception:
            if len(rank_region.shape) == 2:
                gray = rank_region
            else:
                return None

        h, w = gray.shape[:2]
        if h < 3 or w < 3:
            return None

        # Upscale aggressively for tiny rank crops.
        scale = max(3, min(8, 150 // max(h, 1)))
        gray = cv2.resize(
            gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC
        )

        # Generate multiple binary candidates
        candidates: list[Any] = []

        # Strategy 1: OTSU
        _, bin_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(bin_otsu)

        # Strategy 2: CLAHE + OTSU
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
        enhanced = clahe.apply(gray)
        _, bin_clahe = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(bin_clahe)

        # Strategy 3: Fixed threshold (good for white card background)
        _, bin_fixed = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        candidates.append(bin_fixed)

        # Strategy 4: Adaptive threshold
        bin_adapt = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 15, 5,
        )
        candidates.append(bin_adapt)

        # Try each candidate with both polarities
        if _pytesseract is not None:
            for binary in candidates:
                # Ensure dark text on white background
                white_frac = float(np.mean(binary > 127))
                if white_frac < 0.5:
                    binary = cv2.bitwise_not(binary)

                rank = self._tesseract_rank(binary)
                if rank:
                    return rank

                # Try inverted
                rank = self._tesseract_rank(cv2.bitwise_not(binary))
                if rank:
                    return rank

        return None

    def _tesseract_rank(self, binary_image: Any) -> str | None:
        """Run Tesseract on a clean binary image to read the rank.

        Includes a timeout guard to prevent Tesseract from hanging on
        pathological inputs (very small or low-contrast images).
        """
        if _pytesseract is None:
            return None

        np = _np
        if np is not None:
            # Size guard: skip tiny images that cause Tesseract to hang
            h, w = binary_image.shape[:2]
            if h < 15 or w < 10:
                return None
            # Skip images with no text content (all white or all black)
            white_frac = float(np.mean(binary_image > 127))
            if white_frac > 0.98 or white_frac < 0.02:
                return None

        try:
            # PSM 10 = single character, PSM 7 = single text line
            for psm in (10, 7):
                config = (
                    f"--psm {psm} "
                    "-c tessedit_char_whitelist=AaKkQqJjTt0123456789"
                )
                text = _pytesseract.image_to_string(
                    binary_image, config=config, timeout=3,
                ).strip()
                text = re.sub(r"[^AaKkQqJjTt0-9]", "", text)
                if not text:
                    continue

                # Take the first valid rank token
                rank = self._parse_rank_text(text)
                if rank:
                    return rank
        except Exception:
            pass

        return None

    def _parse_rank_text(self, text: str) -> str | None:
        """Parse OCR text into a canonical rank character."""
        text = text.strip()
        if not text:
            return None

        # Try "10" first (two chars)
        if "10" in text:
            return "T"

        # Single character lookup
        for char in text:
            mapped = self._RANK_MAP.get(char)
            if mapped and mapped in self._VALID_RANKS:
                return mapped

        return None

    # ── PPPoker suit reference colours (BGR) ─────────────────────
    # Measured from actual MuMu 12 renders of PPPoker suit symbols.
    # Each tuple: (B, G, R) as float32 for distance calculation.
    _SUIT_REF_COLORS: dict[str, list[tuple[float, float, float]]] = {
        # Hearts – red.  Measured from MuMu 12 PPPoker crops:
        #   Dim:    BGR ≈ (43,46,77),  (45,49,79),  (92,63,67)
        #   Bright: BGR ≈ (84,110,184), (82,81,229), (115,115,255)
        "h": [
            (43.0, 46.0, 77.0),    # dim hearts (suit symbol area)
            (45.0, 49.0, 79.0),    # dim hearts variant
            (92.0, 63.0, 67.0),    # reddish hearts
            (84.0, 110.0, 184.0),  # bright hearts (card face)
            (82.0, 81.0, 229.0),   # very bright red
            (115.0, 115.0, 255.0), # pure bright red
        ],
        # Clubs – green.  Measured from MuMu 12 PPPoker crops:
        #   H≈52: BGR ≈ (48,150,73), (62,157,86), (54,154,79)
        #   H≈77: BGR ≈ (63,145,38), (77,119,18), (81,120,25)
        "c": [
            (48.0, 150.0, 73.0),   # standard MuMu green
            (62.0, 157.0, 86.0),   # lighter green
            (54.0, 154.0, 79.0),   # mid green
            (63.0, 145.0, 38.0),   # dark green (H≈77)
            (77.0, 119.0, 18.0),   # olive green (H≈78)
            (81.0, 120.0, 25.0),   # teal green
        ],
        # Diamonds – blue.  Measured from MuMu 12 PPPoker crops:
        #   BGR ≈ (237,103,36), very consistent across all crops
        "d": [
            (237.0, 103.0, 36.0),  # standard MuMu blue (measured)
            (237.0, 104.0, 38.0),  # slight variant
            (220.0, 100.0, 40.0),  # dimmer blue
            (200.0, 90.0, 30.0),   # dark blue
        ],
    }

    def _detect_suit_color(self, region: Any) -> str | None:
        """Detect card suit using BGR Euclidean distance to reference colours.

        This replaces the old HSV-hue-range approach which was too noisy
        due to green table felt bleed and gold border interference.

        Strategy:
        1. Focus on the **suit symbol area** (upper-left, below rank text)
           to avoid felt bleed and decorative borders.
        2. Filter out white/near-white (card background) and gold border pixels.
        3. For each remaining pixel find the closest suit reference colour.
        4. If mostly dark pixels → spades.
        5. Otherwise → suit with most votes wins.
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return None

        h_reg, w_reg = region.shape[:2]

        # ── Focus on the suit symbol area ──────────────────────────
        # In a PPPoker card crop, the layout is:
        #   Top 30-35%: rank text
        #   35-60%: suit symbol (small coloured icon)
        #   Below: card art / background
        # We focus on y=[30%..60%] x=[5%..55%] to hit the suit symbol.
        y_start = max(1, int(h_reg * 0.30))
        y_end = min(h_reg - 1, int(h_reg * 0.60))
        x_start = max(1, int(w_reg * 0.05))
        x_end = min(w_reg - 1, int(w_reg * 0.55))

        suit_roi = region[y_start:y_end, x_start:x_end]
        if suit_roi.shape[0] < 3 or suit_roi.shape[1] < 3:
            suit_roi = region  # fallback to full region if too small

        # Also prepare inner region (cropped margins) as fallback
        margin_x = max(2, int(w_reg * 0.12))
        margin_y = max(2, int(h_reg * 0.08))
        inner = region[margin_y:h_reg - margin_y, margin_x:w_reg - margin_x]
        if inner.shape[0] < 5 or inner.shape[1] < 5:
            inner = region

        # Try suit ROI first, then inner as fallback
        for roi in [suit_roi, inner]:
            result = self._classify_suit_bgr(roi)
            if result is not None:
                return result

        return None

    def _classify_suit_bgr(self, roi: Any) -> str | None:
        """Classify suit from a BGR ROI using colour distance."""
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return None

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        s_ch = hsv[:, :, 1]
        v_ch = hsv[:, :, 2]

        # ── Filter out background / noise pixels ──────────────────
        # White card background: high V, low S
        # Gold borders: H ≈ 15-35, S > 80 — exclude them
        h_ch = hsv[:, :, 0]
        gold_mask = (h_ch > 14) & (h_ch < 36) & (s_ch > 60)

        # Coloured suit pixels: must have some saturation and not be
        # pure white or pure black
        # Note: no upper V limit — bright red hearts have V=255.
        # White background already excluded by S > 30.
        coloured = (s_ch > 30) & (v_ch > 50) & ~gold_mask

        # Dark pixels (potential spades — black suit)
        dark = (v_ch < 65) & (s_ch < 80)

        n_coloured = int(np.sum(coloured))
        n_dark = int(np.sum(dark))
        total_px = roi.shape[0] * roi.shape[1]
        min_pixels = max(5, int(total_px * 0.01))

        if n_coloured >= min_pixels:
            # Get BGR values of coloured pixels
            bgr_pixels = roi[coloured].astype(np.float32)  # shape (N, 3)

            # ── Nearest-neighbour voting ───────────────────────────
            # For each pixel, find the suit whose reference colour is
            # closest, then vote for that suit.  This avoids the per-suit
            # distance-threshold approach which suffered from overlapping
            # regions in colour space.
            suits = list(self._SUIT_REF_COLORS.keys())  # ["h", "c", "d"]
            all_refs: list[tuple[float, float, float]] = []
            suit_labels: list[str] = []
            for suit in suits:
                for ref in self._SUIT_REF_COLORS[suit]:
                    all_refs.append(ref)
                    suit_labels.append(suit)

            ref_arr = np.array(all_refs, dtype=np.float32)  # shape (R_total, 3)
            # bgr_pixels: (N, 1, 3), ref_arr: (1, R, 3) → dists: (N, R)
            diffs = bgr_pixels[:, None, :] - ref_arr[None, :, :]
            dists = np.sqrt(np.sum(diffs ** 2, axis=2))  # (N, R_total)

            # For each pixel, find the closest reference
            closest_idx = np.argmin(dists, axis=1)  # (N,)
            closest_dist = dists[np.arange(len(dists)), closest_idx]  # (N,)

            # Only count pixels whose closest match is within max distance
            max_dist = 160.0
            valid = closest_dist < max_dist

            votes: dict[str, int] = {"h": 0, "c": 0, "d": 0}
            for i, label in enumerate(suit_labels):
                mask = (closest_idx == i) & valid
                votes[label] = votes.get(label, 0) + int(np.sum(mask))

            best_suit = max(votes, key=lambda k: votes[k])
            best_count = votes[best_suit]

            # Ensure winner has meaningful pixel count
            if best_count >= min_pixels:
                sorted_counts = sorted(votes.values(), reverse=True)
                # Winner should lead the runner-up by at least 1.3×
                if len(sorted_counts) < 2 or sorted_counts[1] == 0 or sorted_counts[0] >= sorted_counts[1] * 1.3:
                    return best_suit

            # Even if below min_pixels, if we have ANY votes and dark
            # pixels don't clearly dominate, prefer the colour vote
            # over defaulting to spades.
            if best_count > 0 and n_dark < n_coloured * 2:
                return best_suit

        # ── Check for spades (black suit) ──────────────────────────
        if n_dark >= min_pixels:
            # Spades only if dark pixels DOMINATE — significantly more
            # dark pixels than coloured ones.  This prevents green cards
            # with some dark text/borders from being classified as spades.
            if n_dark >= n_coloured * 2:
                return "s"

        # Spades fallback: gray-level check — only when very few coloured
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        dark_text = (gray < 65) & (gray > 5)
        n_dark_text = int(np.sum(dark_text))
        if n_dark_text >= min_pixels * 2 and n_coloured < min_pixels:
            return "s"

        return None

    # ── Debug helpers ──────────────────────────────────────────────

    def _save_debug(
        self,
        frame: Any,
        hero_cards: list[str],
        board_cards: list[str],
        hero_rect: tuple[int, int, int, int],
        board_rect: tuple[int, int, int, int] | None,
    ) -> None:
        """Save annotated debug frame with card regions drawn."""
        cv2 = _cv2
        if cv2 is None:
            return
        try:
            os.makedirs(self._debug_dir, exist_ok=True)
            annotated = frame.copy()
            # Draw hero region
            x1, y1, x2, y2 = hero_rect
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                annotated,
                f"HERO: {','.join(hero_cards)}",
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
            )
            # Draw board region
            if board_rect is not None:
                bx1, by1, bx2, by2 = board_rect
                cv2.rectangle(annotated, (bx1, by1), (bx2, by2), (255, 0, 0), 2)
                cv2.putText(
                    annotated,
                    f"BOARD: {','.join(board_cards)}",
                    (bx1, by1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2,
                )
            stamp = int(time.time() * 1000)
            path = os.path.join(self._debug_dir, f"{stamp}_cards.png")
            cv2.imwrite(path, annotated)
        except Exception:
            pass

    def _save_region_debug(
        self,
        region: Any,
        bboxes: list[tuple[int, int, int, int]],
        cards: list[str],
        zone: str,
    ) -> None:
        """Save individual card crops for debugging."""
        cv2 = _cv2
        if cv2 is None:
            return
        try:
            os.makedirs(self._debug_dir, exist_ok=True)
            stamp = int(time.time() * 1000)

            # Full region with bboxes drawn
            annotated = region.copy()
            for i, (x, y, w, h) in enumerate(bboxes):
                cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 0, 255), 1)
                label = cards[i] if i < len(cards) else "?"
                cv2.putText(
                    annotated, label, (x, y - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1,
                )
            path = os.path.join(self._debug_dir, f"{stamp}_{zone}_region.png")
            cv2.imwrite(path, annotated)

            # Individual card crops
            for i, (x, y, w, h) in enumerate(bboxes):
                crop = region[y:y + h, x:x + w]
                crop_path = os.path.join(
                    self._debug_dir, f"{stamp}_{zone}_card{i}.png"
                )
                cv2.imwrite(crop_path, crop)
        except Exception:
            pass

    # ── Utilities ──────────────────────────────────────────────────

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

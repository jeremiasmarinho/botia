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
    _HERO_Y_OFFSET_BOTTOM: int = -260   # above button_y
    _HERO_X_HALF_WIDTH: int = 230       # ± from table centre

    # -- Board card region geometry (relative to pot / estimated position) --
    # Board cards sit just below the pot indicator in PPPoker.
    _BOARD_Y_OFFSET_TOP: int = 20       # below pot_y
    _BOARD_Y_OFFSET_BOTTOM: int = 130   # below pot_y
    _BOARD_X_HALF_WIDTH: int = 230      # ± from pot centre
    # Fallback: if pot is not detected, estimate board position relative
    # to buttons.  Board is approximately 700-800 px above the buttons.
    _BOARD_FALLBACK_Y_OFFSET: int = -750  # above button_y

    # -- Card segmentation thresholds --------------------------------------
    _BRIGHT_THRESHOLD: int = 200        # grayscale value to consider "bright"
    _MIN_CARD_WIDTH: int = 20           # minimum contour width for a card
    _MAX_CARD_WIDTH: int = 100          # maximum contour width
    _MIN_CARD_HEIGHT: int = 30          # minimum contour height
    _MAX_CARD_HEIGHT: int = 120         # maximum contour height
    _MIN_CARD_ASPECT: float = 0.4       # min width/height ratio
    _MAX_CARD_ASPECT: float = 1.0       # max width/height ratio

    def __init__(self) -> None:
        self._debug = os.getenv("TITAN_CARD_READER_DEBUG", "0").strip() == "1"
        self._debug_dir = os.getenv(
            "TITAN_CARD_READER_DEBUG_DIR",
            os.path.join("reports", "debug_cards"),
        ).strip()
        self._enabled = os.getenv(
            "TITAN_CARD_READER_ENABLED", "1"
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
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return []

        h_r, w_r = region.shape[:2]
        if h_r < 20 or w_r < 20:
            return []

        # Convert to grayscale and threshold for bright regions (card faces)
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        _, bright_mask = cv2.threshold(
            gray, self._BRIGHT_THRESHOLD, 255, cv2.THRESH_BINARY
        )

        # Morphological closing to fill small gaps in card faces
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_CLOSE, kernel)

        # Find contours of bright regions
        contours, _ = cv2.findContours(
            bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Filter contours by size and aspect ratio to find card-shaped regions
        card_bboxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < self._MIN_CARD_WIDTH or w > self._MAX_CARD_WIDTH:
                continue
            if h < self._MIN_CARD_HEIGHT or h > self._MAX_CARD_HEIGHT:
                continue
            aspect = w / max(h, 1)
            if aspect < self._MIN_CARD_ASPECT or aspect > self._MAX_CARD_ASPECT:
                continue
            card_bboxes.append((x, y, w, h))

        if not card_bboxes:
            # Fallback: if no individual cards found via contours,
            # try splitting the bright region into equal segments.
            card_bboxes = self._split_by_brightness_columns(bright_mask, h_r)

        if not card_bboxes:
            return []

        # Sort by x position (left to right)
        card_bboxes.sort(key=lambda b: b[0])

        # Merge overlapping bboxes (cards that got split by contour noise)
        card_bboxes = self._merge_overlapping(card_bboxes)

        # Read each card
        cards: list[str] = []
        seen_tokens: set[str] = set()
        for x, y, w, h in card_bboxes:
            card_crop = region[y:y + h, x:x + w]
            token = self._read_single_card(card_crop)
            if token and token not in seen_tokens:
                cards.append(token)
                seen_tokens.add(token)

        if self._debug:
            self._save_region_debug(region, card_bboxes, cards, zone)

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

        # Threshold: column is "card" if ≥ 30% of its pixels are bright
        is_card_col = col_brightness >= 0.30

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

        # Convert segments to bboxes (full region height)
        bboxes: list[tuple[int, int, int, int]] = []
        for x_start, x_end in segments:
            seg_w = x_end - x_start
            # If segment is very wide, it might be multiple cards merged.
            # Try to split it into ~card-width pieces.
            if seg_w > self._MAX_CARD_WIDTH * 1.5:
                n_cards = max(1, round(seg_w / 42))  # ~42px per card
                card_w = seg_w // n_cards
                for i in range(n_cards):
                    cx = x_start + i * card_w
                    cw = card_w if i < n_cards - 1 else (x_end - cx)
                    bboxes.append((cx, 0, cw, region_height))
            else:
                bboxes.append((x_start, 0, seg_w, region_height))

        return bboxes

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
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None or card_crop is None:
            return None

        h, w = card_crop.shape[:2]
        if h < 10 or w < 10:
            return None

        # Use the upper portion (rank + suit symbol area)
        # PPPoker shows rank in the top ~40-50% of the card
        rank_h = max(10, int(h * 0.55))
        rank_region = card_crop[0:rank_h, :]

        # Read rank via OCR
        rank = self._ocr_rank(rank_region)

        # Detect suit via colour analysis
        suit = self._detect_suit_color(rank_region)

        if rank and suit:
            token = f"{rank}{suit}"
            return token

        return None

    def _ocr_rank(self, rank_region: Any) -> str | None:
        """OCR the rank character from the card's top region.

        Pipeline:
        1. Convert to grayscale
        2. Upscale 3× for better OCR accuracy
        3. Binary threshold (OTSU)
        4. Invert if needed (dark text on white background)
        5. Tesseract with single-char mode (PSM 10) and rank whitelist
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

        # Upscale for better OCR
        scale = max(1, min(4, 80 // max(h, 1)))
        if scale > 1:
            gray = cv2.resize(
                gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC
            )

        # Threshold
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Ensure dark text on white background (PPPoker has coloured text on
        # white card — after thresholding the text could be either polarity).
        white_frac = float(np.mean(binary > 127))
        if white_frac < 0.5:
            binary = cv2.bitwise_not(binary)

        # Try Tesseract first
        if _pytesseract is not None:
            rank = self._tesseract_rank(binary)
            if rank:
                return rank

        # Fallback: try with inverted image
        if _pytesseract is not None:
            inverted = cv2.bitwise_not(binary)
            rank = self._tesseract_rank(inverted)
            if rank:
                return rank

        return None

    def _tesseract_rank(self, binary_image: Any) -> str | None:
        """Run Tesseract on a clean binary image to read the rank."""
        if _pytesseract is None:
            return None

        try:
            # PSM 10 = single character, PSM 7 = single text line
            for psm in (10, 7, 8):
                config = (
                    f"--psm {psm} "
                    "-c tessedit_char_whitelist=AaKkQqJjTt0123456789"
                )
                text = _pytesseract.image_to_string(
                    binary_image, config=config
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

    def _detect_suit_color(self, region: Any) -> str | None:
        """Detect card suit by dominant non-white colour in the region.

        PPPoker suit colours (in BGR / HSV):
            ♥ hearts   = red       (H ≈ 0-10 or 160-180, high S)
            ♠ spades   = black     (low S, low V — or very dark)
            ♦ diamonds = blue      (H ≈ 100-130, high S)
            ♣ clubs    = green     (H ≈ 35-85, high S)
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return None

        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        h_ch = hsv[:, :, 0]   # Hue [0-180]
        s_ch = hsv[:, :, 1]   # Saturation [0-255]
        v_ch = hsv[:, :, 2]   # Value [0-255]

        # Mask out white/very bright pixels (card background)
        # and very dark pixels (should not happen on white card)
        non_bg = (s_ch > 40) & (v_ch > 30) & (v_ch < 240)

        # Also consider pure black / very dark pixels separately (spades)
        dark_mask = (v_ch < 60) & (s_ch < 80)

        n_coloured = int(np.sum(non_bg))
        n_dark = int(np.sum(dark_mask))

        # If very few coloured or dark pixels, we can't determine suit
        min_pixels = max(10, int(region.shape[0] * region.shape[1] * 0.02))

        # Count pixels for each colour range
        if n_coloured >= min_pixels:
            hue_vals = h_ch[non_bg]
            sat_vals = s_ch[non_bg]

            # Red: H in [0, 10] or [160, 180] with high saturation
            red_mask = ((hue_vals < 12) | (hue_vals > 158)) & (sat_vals > 60)
            n_red = int(np.sum(red_mask))

            # Blue: H in [95, 135]
            blue_mask = (hue_vals > 95) & (hue_vals < 135) & (sat_vals > 50)
            n_blue = int(np.sum(blue_mask))

            # Green: H in [35, 85]
            green_mask = (hue_vals > 35) & (hue_vals < 85) & (sat_vals > 50)
            n_green = int(np.sum(green_mask))

            # Pick the dominant colour
            counts = {"h": n_red, "d": n_blue, "c": n_green}
            best_suit = max(counts, key=lambda k: counts[k])
            best_count = counts[best_suit]

            if best_count >= min_pixels:
                return best_suit

        # If no coloured pixels dominate, check for black (spades)
        if n_dark >= min_pixels:
            return "s"

        # Last resort: check if most non-background pixels are dark-ish
        # (for spades on a white card)
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        dark_text = (gray < 100) & (gray > 5)
        n_dark_text = int(np.sum(dark_text))
        if n_dark_text >= min_pixels:
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

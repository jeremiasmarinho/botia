"""PPPoker card reader — **template matching** for robust card identification.

Replaces the fragile Tesseract-OCR + HSV pipeline with direct image
comparison against reference card templates.

Architecture
------------
1. **Template loading** — loads all 52 card images from ``assets/cards/``
   and normalises them to a canonical size for fast comparison.
2. **Region estimation** — uses detected button positions from YOLO as
   anchors to calculate where hero and board cards sit on screen.
3. **Multi-scale sliding-window matching** — runs ``cv2.matchTemplate``
   with each template across the region at 3 scales.  This finds cards
   regardless of contour quality, background noise, or colour shifts.
4. **Non-max suppression** — deduplicates overlapping detections.
5. **Fallback contour + crop matching** — if sliding-window finds too
   few cards, segments via brightness contours and matches each crop
   against all 52 templates using ``cv2.absdiff``.

Key advantages over OCR + HSV
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- **No Tesseract dependency** — works even if Tesseract is broken or
  absent.  Template matching is pure OpenCV.
- **Deterministic** — same pixel pattern always maps to the same card.
- **Colour-invariant** — uses grayscale matching, immune to HSV hue
  shifts from emulator rendering differences.
- **Proven approach** — used by EdjeElectronics/OpenCV-Playing-Card-Detector
  (737 ★ on GitHub).

PPPoker card visual characteristics
------------------------------------
- White/cream background with rounded corners.
- Gold border on hero cards.
- Large rank character (top-left corner) and suit symbol below.
- Suit colours:  ♥ = red  |  ♠ = black  |  ♦ = blue  |  ♣ = green.
- Cards are pixel-identical across sessions for the same emulator
  resolution (720×1280, DPI 320).

Environment variables
---------------------
``TITAN_TEMPLATE_READER_ENABLED``  Set to ``1`` to enable (default: ``1``).
``TITAN_TEMPLATE_READER_DEBUG``    Set to ``1`` to save debug images.
``TITAN_TEMPLATE_READER_DIR``      Template image directory override.
``TITAN_TEMPLATE_MATCH_THRESHOLD`` Match confidence threshold (default: 0.55).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

_cv2: Any | None = None
_np: Any | None = None


def _ensure_deps() -> bool:
    global _cv2, _np
    if _cv2 is not None and _np is not None:
        return True
    try:
        import cv2
        _cv2 = cv2
    except ImportError:
        return False
    try:
        import numpy as np
        _np = np
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TemplateMatch:
    """A single card detection result."""
    token: str          # e.g. "Ah", "Td"
    x: int              # left edge in region coordinates
    y: int              # top edge in region coordinates
    w: int              # bounding-box width
    h: int              # bounding-box height
    confidence: float   # match score [0-1]
    scale: float        # scale factor used


# ---------------------------------------------------------------------------
# The 52 canonical rank/suit tokens
# ---------------------------------------------------------------------------

_ALL_RANKS = "A23456789TJQK"
_ALL_SUITS = "hsdc"
_VALID_TOKENS = {f"{r}{s}" for r in _ALL_RANKS for s in _ALL_SUITS}


# ---------------------------------------------------------------------------
# TemplateCardReader
# ---------------------------------------------------------------------------

class TemplateCardReader:
    """Card detection via template matching against reference card images.

    Typical usage::

        reader = TemplateCardReader()
        hero, board = reader.read_cards(frame, action_points, pot_xy)
        # hero  = ["Qh", "6c", "5c", "5s", "8c"]   (PLO5)
        # board = ["6s", "9c", "3c", "3d", "7c"]
    """

    # -- Canonical template size -------------------------------------------
    # All templates are resized to this before comparison.
    CANONICAL_W: int = 44
    CANONICAL_H: int = 66

    # -- Region geometry (720×1280 reference) ------------------------------
    _HERO_Y_OFFSET_TOP: int = -420
    _HERO_Y_OFFSET_BOTTOM: int = -150
    _HERO_X_HALF_WIDTH: int = 310

    _BOARD_Y_OFFSET_TOP: int = -40
    _BOARD_Y_OFFSET_BOTTOM: int = 200
    _BOARD_X_HALF_WIDTH: int = 260
    _BOARD_FALLBACK_Y_OFFSET: int = -760

    # -- Matching thresholds -----------------------------------------------
    _MATCH_THRESHOLD: float = 0.55       # TM_CCOEFF_NORMED minimum
    _ABSDIFF_THRESHOLD: float = 0.22     # max normalised diff for crop matching
    _NMS_OVERLAP_THRESH: float = 0.40    # IoU threshold for NMS

    # -- Contour segmentation (for fallback) --------------------------------
    _BRIGHT_THRESHOLD: int = 140
    _MIN_CARD_WIDTH: int = 28
    _MAX_CARD_WIDTH: int = 120
    _MIN_CARD_HEIGHT: int = 40
    _MAX_CARD_HEIGHT: int = 150

    # -- Scales for multi-scale matching -----------------------------------
    _SCALES: tuple[float, ...] = (0.85, 0.95, 1.0, 1.08, 1.18)

    def __init__(
        self,
        template_dir: str | None = None,
        match_threshold: float | None = None,
    ) -> None:
        self._enabled = os.getenv(
            "TITAN_TEMPLATE_READER_ENABLED", "1"
        ).strip() in {"1", "true", "yes", "on"}

        self._debug = os.getenv(
            "TITAN_TEMPLATE_READER_DEBUG", "0"
        ).strip() == "1"
        self._debug_dir = os.path.join("reports", "debug_template_cards")

        # Resolve template directory — prefer MuMu-native auto-learned
        # templates (from OCR auto-template learning) over generic ones.
        self._mumu_template_dir = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__), "..", "assets", "cards_mumu"
            )
        )
        if template_dir is None:
            template_dir = os.getenv(
                "TITAN_TEMPLATE_READER_DIR", ""
            ).strip()
        if not template_dir:
            # Auto-detect: look relative to this file, or project root
            candidates = [
                os.path.join(os.path.dirname(__file__), "..", "assets", "cards"),
                os.path.join("assets", "cards"),
                os.path.join(os.path.dirname(__file__), "assets", "cards"),
            ]
            for cand in candidates:
                if os.path.isdir(cand):
                    template_dir = cand
                    break
            else:
                template_dir = os.path.join("assets", "cards")

        self._template_dir = os.path.normpath(template_dir)
        self._match_threshold = match_threshold or float(
            os.getenv("TITAN_TEMPLATE_MATCH_THRESHOLD", str(self._MATCH_THRESHOLD))
        )

        # Templates: {token → grayscale canonical image}
        self._templates: dict[str, Any] = {}
        # Multi-scale templates: {(token, scale_idx) → resized grayscale}
        self._scaled_templates: dict[tuple[str, int], Any] = {}

        if _ensure_deps():
            self._load_templates()

    # ── Properties ─────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._templates)

    @property
    def template_count(self) -> int:
        return len(self._templates)

    # ── Template loading ───────────────────────────────────────────

    def _load_templates(self) -> None:
        """Load and normalise all card template images.

        Loads from two directories with priority:
        1. ``assets/cards_mumu/`` — MuMu-native templates (auto-learned
           from OCR results) → highest priority, best match quality.
        2. ``assets/cards/``     — generic/LDPlayer templates → fallback.

        If a token exists in both directories, the MuMu-native version
        is used because it matches MuMu's rendering more closely.
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return

        loaded = 0

        # Helper to load from a single directory
        def _load_dir(dirpath: str) -> int:
            nonlocal loaded
            count = 0
            if not os.path.isdir(dirpath):
                return 0
            for fname in sorted(os.listdir(dirpath)):
                if not fname.lower().endswith(".png"):
                    continue
                token = fname[:-4]
                if token not in _VALID_TOKENS:
                    continue
                # Skip if already loaded (MuMu takes priority over generic)
                if token in self._templates:
                    continue

                filepath = os.path.join(dirpath, fname)
                img = cv2.imread(filepath, cv2.IMREAD_COLOR)
                if img is None:
                    continue

                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                canonical = cv2.resize(
                    gray,
                    (self.CANONICAL_W, self.CANONICAL_H),
                    interpolation=cv2.INTER_AREA,
                )
                self._templates[token] = canonical
                count += 1
            return count

        # Load MuMu-native templates first (highest priority)
        n_mumu = _load_dir(self._mumu_template_dir)

        # Fill gaps with generic templates
        n_generic = _load_dir(self._template_dir)

        loaded = n_mumu + n_generic

        # Pre-compute scaled versions for multi-scale matching
        for si, scale in enumerate(self._SCALES):
            sw = max(8, int(self.CANONICAL_W * scale))
            sh = max(12, int(self.CANONICAL_H * scale))
            for token, canon in self._templates.items():
                resized = cv2.resize(canon, (sw, sh), interpolation=cv2.INTER_AREA)
                self._scaled_templates[(token, si)] = resized

        logger.info(
            "Loaded %d card templates (%d MuMu-native, %d generic) with %d scaled variants",
            loaded, n_mumu, n_generic, len(self._scaled_templates),
        )

    # ── Public API ─────────────────────────────────────────────────

    def read_cards(
        self,
        frame: Any,
        action_points: dict[str, tuple[int, int]],
        pot_xy: tuple[int, int] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Read hero and board cards from a captured frame.

        Args:
            frame:         BGR numpy array (720×1280 or similar).
            action_points: ``{action_name: (x, y)}`` from YOLO detection.
            pot_xy:        Centre of pot indicator, or ``None``.

        Returns:
            ``(hero_cards, board_cards)`` — sorted left-to-right.
        """
        if not self.enabled or not _ensure_deps():
            return [], []
        if frame is None or _np is None:
            return [], []

        cv2 = _cv2
        np = _np
        h_frame, w_frame = frame.shape[:2]

        # -- Filter buttons to single table --
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

        # -- Hero region --
        hero_y1 = max(0, button_y + self._HERO_Y_OFFSET_TOP)
        hero_y2 = min(h_frame, button_y + self._HERO_Y_OFFSET_BOTTOM)
        hero_x1 = max(0, table_center_x - self._HERO_X_HALF_WIDTH)
        hero_x2 = min(w_frame, table_center_x + self._HERO_X_HALF_WIDTH)

        hero_cards: list[str] = []
        if hero_y2 > hero_y1 + 20 and hero_x2 > hero_x1 + 20:
            hero_crop = frame[hero_y1:hero_y2, hero_x1:hero_x2]
            hero_cards = self._detect_cards_in_region(hero_crop, "hero")

        # -- Board region --
        if pot_xy is not None:
            board_cx, board_cy = int(pot_xy[0]), int(pot_xy[1])
        else:
            board_cx = table_center_x
            board_cy = max(0, button_y + self._BOARD_FALLBACK_Y_OFFSET)

        board_y1 = max(0, board_cy + self._BOARD_Y_OFFSET_TOP)
        board_y2 = min(h_frame, board_cy + self._BOARD_Y_OFFSET_BOTTOM)
        board_x1 = max(0, board_cx - self._BOARD_X_HALF_WIDTH)
        board_x2 = min(w_frame, board_cx + self._BOARD_X_HALF_WIDTH)

        board_cards: list[str] = []
        if board_y2 > board_y1 + 20 and board_x2 > board_x1 + 20:
            board_crop = frame[board_y1:board_y2, board_x1:board_x2]
            board_cards = self._detect_cards_in_region(board_crop, "board")

        if self._debug:
            self._save_debug(
                frame, hero_cards, board_cards,
                (hero_x1, hero_y1, hero_x2, hero_y2),
                (board_x1, board_y1, board_x2, board_y2),
            )

        return hero_cards, board_cards

    # ── Core detection ─────────────────────────────────────────────

    def _detect_cards_in_region(
        self,
        region: Any,
        zone: str,
    ) -> list[str]:
        """Detect and identify cards within a cropped region.

        Strategy:
        1. Sliding-window matchTemplate across the region (most robust).
        2. If that finds too few, try contour → crop → absdiff matching.
        3. Merge results from both methods, deduplicate.
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return []

        h_r, w_r = region.shape[:2]
        if h_r < 20 or w_r < 20:
            return []

        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

        # Method 1: Sliding-window template matching
        matches_sw = self._sliding_window_match(gray)

        # Method 2: Contour-based crop + absdiff (fallback)
        if len(matches_sw) < 3:
            matches_crop = self._contour_crop_match(region, gray)
            # Merge: add crop matches that don't overlap with SW matches
            matches_sw = self._merge_match_lists(matches_sw, matches_crop)

        if not matches_sw:
            return []

        # Sort by x (left to right)
        matches_sw.sort(key=lambda m: m.x)

        # Extract unique tokens preserving order
        seen: set[str] = set()
        result: list[str] = []
        for m in matches_sw:
            if m.token not in seen:
                result.append(m.token)
                seen.add(m.token)

        if self._debug:
            self._save_region_debug(region, matches_sw, zone)

        return result

    def _sliding_window_match(self, gray_region: Any) -> list[TemplateMatch]:
        """Scan all 52 templates across the region at multiple scales.

        Uses ``cv2.matchTemplate`` with ``TM_CCOEFF_NORMED`` for each
        (template, scale) pair.  Peaks above threshold are collected,
        then non-max suppression removes overlapping detections.
        """
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return []

        h_r, w_r = gray_region.shape[:2]
        raw_matches: list[TemplateMatch] = []

        for si, scale in enumerate(self._SCALES):
            for token in self._templates:
                tmpl = self._scaled_templates.get((token, si))
                if tmpl is None:
                    continue

                th, tw = tmpl.shape[:2]
                if tw >= w_r or th >= h_r:
                    continue  # template larger than region

                result = cv2.matchTemplate(
                    gray_region, tmpl, cv2.TM_CCOEFF_NORMED
                )

                # Find all locations above threshold
                locs = np.where(result >= self._match_threshold)
                for pt_y, pt_x in zip(*locs):
                    score = float(result[pt_y, pt_x])
                    raw_matches.append(TemplateMatch(
                        token=token,
                        x=int(pt_x),
                        y=int(pt_y),
                        w=tw,
                        h=th,
                        confidence=score,
                        scale=scale,
                    ))

        if not raw_matches:
            return []

        # Non-max suppression
        return self._nms(raw_matches)

    def _contour_crop_match(
        self,
        region: Any,
        gray: Any,
    ) -> list[TemplateMatch]:
        """Fallback: find card-shaped contours, crop each, match via absdiff."""
        cv2 = _cv2
        np = _np
        if cv2 is None or np is None:
            return []

        # Auto-crop to bright Y band
        _, bright_mask = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
        row_brightness = np.mean(bright_mask > 0, axis=1)
        bright_rows = np.where(row_brightness > 0.05)[0]

        working = gray
        working_color = region
        y_offset = 0
        if len(bright_rows) > 0:
            y_top = max(0, int(bright_rows[0]) - 10)
            y_bot = min(gray.shape[0], int(bright_rows[-1]) + 10)
            if (y_bot - y_top) < gray.shape[0] * 0.85:
                working = gray[y_top:y_bot, :]
                working_color = region[y_top:y_bot, :]
                y_offset = y_top

        h_w, w_w = working.shape[:2]
        if h_w < 15 or w_w < 15:
            return []

        # Find card bboxes via brightness contours
        bboxes: list[tuple[int, int, int, int]] = []
        for thresh in [self._BRIGHT_THRESHOLD, 120, 100]:
            _, mask = cv2.threshold(working, thresh, 255, cv2.THRESH_BINARY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            candidate: list[tuple[int, int, int, int]] = []
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                if w < self._MIN_CARD_WIDTH or h < self._MIN_CARD_HEIGHT:
                    continue
                if h > self._MAX_CARD_HEIGHT:
                    continue
                if w > self._MAX_CARD_WIDTH * 1.5:
                    n = max(2, round(w / 50))
                    cw = w // n
                    for i in range(n):
                        cx = x + i * cw
                        candidate.append((cx, y, cw, h))
                elif w <= self._MAX_CARD_WIDTH:
                    candidate.append((x, y, w, h))
            if len(candidate) > len(bboxes):
                bboxes = candidate

        if not bboxes:
            return []

        # Match each crop against all templates
        matches: list[TemplateMatch] = []
        for bx, by, bw, bh in bboxes:
            crop = working[by:by + bh, bx:bx + bw]
            if crop.shape[0] < 10 or crop.shape[1] < 10:
                continue
            resized = cv2.resize(
                crop, (self.CANONICAL_W, self.CANONICAL_H),
                interpolation=cv2.INTER_AREA,
            )

            best_token: str | None = None
            best_diff = float("inf")
            for token, tmpl in self._templates.items():
                diff = cv2.absdiff(resized, tmpl)
                score = float(_np.sum(diff)) / 255.0 / (
                    self.CANONICAL_W * self.CANONICAL_H
                )
                if score < best_diff:
                    best_diff = score
                    best_token = token

            if best_token and best_diff < self._ABSDIFF_THRESHOLD:
                matches.append(TemplateMatch(
                    token=best_token,
                    x=bx,
                    y=by + y_offset,
                    w=bw,
                    h=bh,
                    confidence=1.0 - best_diff,
                    scale=1.0,
                ))

        return matches

    # ── Non-max suppression ────────────────────────────────────────

    def _nms(self, matches: list[TemplateMatch]) -> list[TemplateMatch]:
        """Non-max suppression: keep best match per spatial location."""
        if not matches:
            return []

        np = _np
        if np is None:
            return matches

        # Sort by confidence descending
        matches.sort(key=lambda m: m.confidence, reverse=True)

        keep: list[TemplateMatch] = []
        for m in matches:
            # Check if this overlaps with any already-kept match
            overlaps = False
            for k in keep:
                iou = self._iou(m, k)
                if iou > self._NMS_OVERLAP_THRESH:
                    overlaps = True
                    break
            if not overlaps:
                keep.append(m)

        return keep

    @staticmethod
    def _iou(a: TemplateMatch, b: TemplateMatch) -> float:
        """Compute intersection-over-union between two matches."""
        x1 = max(a.x, b.x)
        y1 = max(a.y, b.y)
        x2 = min(a.x + a.w, b.x + b.w)
        y2 = min(a.y + a.h, b.y + b.h)
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        if inter == 0:
            return 0.0
        area_a = a.w * a.h
        area_b = b.w * b.h
        union = area_a + area_b - inter
        return inter / max(union, 1)

    def _merge_match_lists(
        self,
        primary: list[TemplateMatch],
        secondary: list[TemplateMatch],
    ) -> list[TemplateMatch]:
        """Merge secondary matches into primary, avoiding spatial overlaps."""
        if not secondary:
            return primary
        if not primary:
            return secondary

        merged = list(primary)
        for s in secondary:
            overlaps = False
            for p in merged:
                if self._iou(s, p) > self._NMS_OVERLAP_THRESH:
                    overlaps = True
                    break
            if not overlaps:
                merged.append(s)
        return merged

    # ── Button clustering (reused from PPPokerCardReader) ──────────

    @staticmethod
    def _cluster_buttons(
        action_points: dict[str, tuple[int, int]],
    ) -> dict[str, tuple[int, int]]:
        """Filter action_points to keep only buttons from one table."""
        primary_names = {"fold", "call", "check", "raise"}
        primaries = [
            (name, int(xy[0]), int(xy[1]))
            for name, xy in action_points.items()
            if name in primary_names
        ]
        if len(primaries) <= 1:
            return action_points

        y_values = [y for _, _, y in primaries]
        best_y = max(
            set(y_values),
            key=lambda yv: sum(1 for y2 in y_values if abs(y2 - yv) < 80),
        )
        y_ok = [(n, x, y) for n, x, y in primaries if abs(y - best_y) < 80]

        if len(y_ok) >= 2:
            x_vals = sorted(x for _, x, _ in y_ok)
            median_x = x_vals[len(x_vals) // 2]
            y_ok = [(n, x, y) for n, x, y in y_ok if abs(x - median_x) < 500]

        if not y_ok:
            return action_points

        filtered = {n: (x, y) for n, x, y in y_ok}

        if filtered:
            fx_vals = [x for _, (x, _) in filtered.items()]
            f_center_x = sum(fx_vals) // len(fx_vals)
            for key, xy in action_points.items():
                if key in primary_names:
                    continue
                if abs(int(xy[0]) - f_center_x) < 400:
                    filtered[key] = xy

        return filtered

    # ── Debug ──────────────────────────────────────────────────────

    def _save_debug(
        self,
        frame: Any,
        hero_cards: list[str],
        board_cards: list[str],
        hero_rect: tuple[int, int, int, int],
        board_rect: tuple[int, int, int, int],
    ) -> None:
        cv2 = _cv2
        if cv2 is None:
            return
        try:
            os.makedirs(self._debug_dir, exist_ok=True)
            annotated = frame.copy()
            x1, y1, x2, y2 = hero_rect
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                annotated,
                f"HERO: {','.join(hero_cards)}",
                (x1, max(y1 - 10, 15)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
            )
            bx1, by1, bx2, by2 = board_rect
            cv2.rectangle(annotated, (bx1, by1), (bx2, by2), (255, 0, 0), 2)
            cv2.putText(
                annotated,
                f"BOARD: {','.join(board_cards)}",
                (bx1, max(by1 - 10, 15)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1,
            )
            stamp = int(time.time() * 1000)
            cv2.imwrite(
                os.path.join(self._debug_dir, f"{stamp}_cards.png"),
                annotated,
            )
        except Exception:
            pass

    def _save_region_debug(
        self,
        region: Any,
        matches: list[TemplateMatch],
        zone: str,
    ) -> None:
        cv2 = _cv2
        if cv2 is None:
            return
        try:
            os.makedirs(self._debug_dir, exist_ok=True)
            annotated = region.copy()
            for m in matches:
                cv2.rectangle(
                    annotated, (m.x, m.y),
                    (m.x + m.w, m.y + m.h),
                    (0, 0, 255), 1,
                )
                cv2.putText(
                    annotated,
                    f"{m.token} {m.confidence:.2f}",
                    (m.x, max(m.y - 3, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1,
                )
            stamp = int(time.time() * 1000)
            cv2.imwrite(
                os.path.join(self._debug_dir, f"{stamp}_{zone}.png"),
                annotated,
            )
        except Exception:
            pass

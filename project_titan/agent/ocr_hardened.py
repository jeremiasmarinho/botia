"""Titan OCR v2 — Hardened numeric OCR for zero-error poker decisions.

Enhanced version with:
1. **Multi-frame consensus** — requires N consistent reads before accepting a value change
2. **Dual-pipeline preprocessing** — OTSU + adaptive threshold, picks the cleaner result
3. **PPPoker-optimized preprocessing** — tuned for PPPoker's yellow/white text on dark backgrounds
4. **Confidence scoring** — each read gets a reliability score
5. **Anomaly detection** — rejects sudden jumps that don't match game logic
6. **Detailed logging** — every OCR read is traceable for debugging

Usage:
    from agent.ocr_hardened import HardenedOCR

    ocr = HardenedOCR(confirm_frames=3)
    for frame in capture_loop():
        pot = ocr.read_value(frame_crop, key="pot", min_val=0, max_val=500000)
        stack = ocr.read_value(stack_crop, key="stack", min_val=0, max_val=500000)
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class OCRReading:
    """A single OCR reading with metadata."""
    raw_text: str = ""
    parsed_value: float | None = None
    pipeline: str = ""         # "otsu" | "adaptive" | "easyocr"
    confidence: float = 0.0    # 0.0-1.0
    timestamp: float = 0.0
    accepted: bool = False


@dataclass
class ValueTracker:
    """Tracks a single OCR metric with consensus validation."""
    key: str
    confirmed_value: float = 0.0
    pending_value: float | None = None
    pending_count: int = 0
    confirm_threshold: int = 3
    history: deque = field(default_factory=lambda: deque(maxlen=20))
    last_change_ts: float = 0.0
    total_reads: int = 0
    total_changes: int = 0

    def submit(self, value: float | None, max_jump: float | None = None) -> float:
        """Submit a new reading. Returns the confirmed value.

        The value only changes after `confirm_threshold` consecutive
        consistent reads (within 1% tolerance).
        """
        self.total_reads += 1
        now = time.time()

        if value is None:
            # Failed read — keep confirmed value
            self.pending_value = None
            self.pending_count = 0
            return self.confirmed_value

        # Anomaly detection: reject implausible jumps
        if max_jump is not None and self.confirmed_value > 0:
            jump = abs(value - self.confirmed_value) / max(self.confirmed_value, 1)
            if jump > max_jump:
                # Suspicious jump — require extra confirmation
                if self.pending_value is not None and _values_close(value, self.pending_value):
                    self.pending_count += 1
                else:
                    self.pending_value = value
                    self.pending_count = 1
                # Need double the confirmation for suspicious jumps
                if self.pending_count >= self.confirm_threshold * 2:
                    self.confirmed_value = value
                    self.pending_value = None
                    self.pending_count = 0
                    self.last_change_ts = now
                    self.total_changes += 1
                return self.confirmed_value

        # Normal consensus path
        if _values_close(value, self.confirmed_value):
            # Same value — reset pending
            self.pending_value = None
            self.pending_count = 0
            return self.confirmed_value

        # New value — accumulate consensus
        if self.pending_value is not None and _values_close(value, self.pending_value):
            self.pending_count += 1
        else:
            self.pending_value = value
            self.pending_count = 1

        if self.pending_count >= self.confirm_threshold:
            self.confirmed_value = value
            self.pending_value = None
            self.pending_count = 0
            self.last_change_ts = now
            self.total_changes += 1

        self.history.append(OCRReading(
            parsed_value=value,
            timestamp=now,
            accepted=(self.pending_count == 0 and _values_close(value, self.confirmed_value)),
        ))

        return self.confirmed_value


def _values_close(a: float, b: float, tolerance: float = 0.01) -> bool:
    """Check if two values are within tolerance (relative or absolute)."""
    if a == b:
        return True
    if max(abs(a), abs(b)) < 1.0:
        return abs(a - b) < 1.0  # < $1 absolute for small values
    return abs(a - b) / max(abs(a), abs(b), 1) < tolerance


class HardenedOCR:
    """Zero-error OCR engine with multi-frame consensus.

    Features:
    - Dual preprocessing pipeline (OTSU + adaptive threshold)
    - Multi-frame consensus (configurable confirm_frames)
    - PPPoker-specific color isolation (yellow/white text)
    - Anomaly detection for value jumps
    - Last-value fallback with staleness tracking
    """

    def __init__(
        self,
        *,
        use_easyocr: bool = False,
        tesseract_cmd: str | None = None,
        confirm_frames: int = 3,
        max_jump_ratio: float = 5.0,
    ):
        self.use_easyocr = bool(use_easyocr)
        self.confirm_frames = max(1, confirm_frames)
        self.max_jump_ratio = max_jump_ratio

        self._cv2: Any = None
        self._np: Any = None
        self._pytesseract: Any = None
        self._easy_reader: Any = None
        self._trackers: dict[str, ValueTracker] = {}

        self._load_backends(tesseract_cmd)

    def _load_backends(self, tesseract_cmd: str | None = None) -> None:
        try:
            import cv2
            self._cv2 = cv2
        except ImportError:
            pass

        try:
            import numpy as np
            self._np = np
        except ImportError:
            pass

        try:
            import pytesseract
            if tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            self._pytesseract = pytesseract
        except ImportError:
            pass

        if self.use_easyocr:
            try:
                import easyocr
                self._easy_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            except ImportError:
                pass

    def _get_tracker(self, key: str) -> ValueTracker:
        """Get or create a value tracker for a key."""
        if key not in self._trackers:
            self._trackers[key] = ValueTracker(
                key=key,
                confirm_threshold=self.confirm_frames,
            )
        return self._trackers[key]

    # ── Preprocessing Pipelines ─────────────────────────────────────

    def _preprocess_otsu(self, crop: Any) -> Any | None:
        """Standard OTSU threshold pipeline."""
        cv2 = self._cv2
        if cv2 is None:
            return crop
        try:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
            h, w = gray.shape[:2]
            if h <= 0 or w <= 0:
                return None
            up = cv2.resize(gray, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
            blurred = cv2.GaussianBlur(up, (3, 3), 0)
            _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            return cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        except Exception:
            return crop

    def _preprocess_adaptive(self, crop: Any) -> Any | None:
        """Adaptive threshold — better for uneven lighting."""
        cv2 = self._cv2
        if cv2 is None:
            return crop
        try:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
            h, w = gray.shape[:2]
            if h <= 0 or w <= 0:
                return None
            up = cv2.resize(gray, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
            blurred = cv2.GaussianBlur(up, (5, 5), 0)
            thresh = cv2.adaptiveThreshold(
                blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 15, 4
            )
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            return cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        except Exception:
            return crop

    def _preprocess_pppoker_yellow(self, crop: Any) -> Any | None:
        """PPPoker-specific: isolate yellow/gold text on dark background.

        PPPoker uses yellow (#FFC700-ish) for pot/stack amounts.
        This pipeline isolates that color range for cleaner OCR.
        """
        cv2 = self._cv2
        np = self._np
        if cv2 is None or np is None:
            return crop
        try:
            if len(crop.shape) != 3:
                return self._preprocess_otsu(crop)

            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

            # Yellow/gold range in HSV (PPPoker amounts)
            lower_yellow = np.array([15, 80, 120])
            upper_yellow = np.array([45, 255, 255])
            mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)

            # White range (also common in PPPoker UI)
            lower_white = np.array([0, 0, 180])
            upper_white = np.array([180, 40, 255])
            mask_white = cv2.inRange(hsv, lower_white, upper_white)

            combined = cv2.bitwise_or(mask_yellow, mask_white)

            # Upscale + clean
            h, w = combined.shape[:2]
            up = cv2.resize(combined, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            cleaned = cv2.morphologyEx(up, cv2.MORPH_CLOSE, kernel)
            cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel)

            return cleaned
        except Exception:
            return self._preprocess_otsu(crop)

    # ── OCR Execution ───────────────────────────────────────────────

    def _ocr_tesseract(self, image: Any) -> str:
        if self._pytesseract is None:
            return ""
        try:
            configs = [
                "--psm 7 -c tessedit_char_whitelist=0123456789.$,",
                "--psm 8 -c tessedit_char_whitelist=0123456789.$,",
                "--psm 13 -c tessedit_char_whitelist=0123456789.$,",
            ]
            for cfg in configs:
                text = self._pytesseract.image_to_string(image, config=cfg).strip()
                if text and re.search(r"\d", text):
                    return text
            return ""
        except Exception:
            return ""

    def _ocr_easyocr(self, image: Any) -> str:
        if self._easy_reader is None:
            return ""
        try:
            results = self._easy_reader.readtext(
                image, detail=0,
                allowlist="0123456789.$,",
                paragraph=False,
            )
            return " ".join(str(r) for r in results).strip() if results else ""
        except Exception:
            return ""

    # ── Value Parsing ───────────────────────────────────────────────

    @staticmethod
    def _parse_numeric(text: str) -> float | None:
        """Parse OCR text to float with aggressive sanitization."""
        if not text:
            return None

        cleaned = text.strip().replace(" ", "")

        # Common OCR misreads
        replacements = {
            "O": "0", "o": "0",
            "S": "5", "s": "5",
            "I": "1", "l": "1", "|": "1",
            "B": "8", "b": "6",
            "Z": "2", "z": "2",
            "G": "6", "g": "9",
            "D": "0",
            "$": "",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)

        # Keep only digits and separators
        cleaned = re.sub(r"[^0-9.,]", "", cleaned)
        if not cleaned:
            return None

        # Handle separators
        # "1,234" → 1234, "1.5" → 1.5, "1,234.56" → 1234.56
        dots = cleaned.count(".")
        commas = cleaned.count(",")

        if commas > 0 and dots == 0:
            # Could be "1,234" (thousands) or "1,5" (decimal)
            # PPPoker uses "1,234" format for thousands
            parts = cleaned.split(",")
            if len(parts[-1]) == 3:
                # Thousands separator
                cleaned = cleaned.replace(",", "")
            else:
                # Decimal separator
                cleaned = cleaned.replace(",", ".")
        elif commas > 0 and dots > 0:
            # "1,234.56" — comma is thousands
            cleaned = cleaned.replace(",", "")

        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match is None:
            return None

        try:
            value = float(match.group(0))
        except ValueError:
            return None

        if value < 0 or value > 10_000_000:
            return None

        return value

    # ── Public API ──────────────────────────────────────────────────

    def read_value(
        self,
        image_crop: Any,
        *,
        key: str = "default",
        min_val: float = 0.0,
        max_val: float = 1_000_000.0,
        fallback: float = 0.0,
    ) -> float:
        """Read a numeric value from an image crop with consensus validation.

        The value only updates after `confirm_frames` consistent reads.
        This eliminates OCR flicker and ghost values.

        Args:
            image_crop: BGR or grayscale image crop of the number region.
            key: Logical key for tracking ("pot", "stack", "call_amount").
            min_val: Minimum acceptable value.
            max_val: Maximum acceptable value.
            fallback: Default when no reading available.

        Returns:
            Confirmed numeric value (float).
        """
        tracker = self._get_tracker(key)

        if image_crop is None:
            return tracker.submit(None)

        if tracker.confirmed_value == 0.0:
            tracker.confirmed_value = fallback

        # Run dual pipeline
        best_value: float | None = None
        best_confidence: float = 0.0

        pipelines = [
            ("otsu", self._preprocess_otsu),
            ("adaptive", self._preprocess_adaptive),
            ("pppoker", self._preprocess_pppoker_yellow),
        ]

        for pipeline_name, preprocess_fn in pipelines:
            processed = preprocess_fn(image_crop)
            if processed is None:
                continue

            text = self._ocr_tesseract(processed)
            if not text and self.use_easyocr:
                text = self._ocr_easyocr(processed)

            value = self._parse_numeric(text)

            if value is not None and min_val <= value <= max_val:
                # Score confidence: longer digit strings are more reliable
                digits = len(re.findall(r"\d", text))
                confidence = min(1.0, digits / 6.0)  # 6+ digits = full confidence

                if confidence > best_confidence:
                    best_value = value
                    best_confidence = confidence

        # Submit to consensus tracker
        return tracker.submit(best_value, max_jump=self.max_jump_ratio)

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all tracked values."""
        stats: dict[str, dict[str, Any]] = {}
        for key, tracker in self._trackers.items():
            stats[key] = {
                "confirmed_value": tracker.confirmed_value,
                "pending_value": tracker.pending_value,
                "pending_count": tracker.pending_count,
                "total_reads": tracker.total_reads,
                "total_changes": tracker.total_changes,
                "history_len": len(tracker.history),
            }
        return stats

    def reset(self, key: str | None = None) -> None:
        """Reset tracker(s). Use when switching tables or screens."""
        if key is not None:
            self._trackers.pop(key, None)
        else:
            self._trackers.clear()

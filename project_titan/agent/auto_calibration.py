"""Project Titan — Auto-Calibration System (Zero-Drift).

Self-validating calibration that:
1. Uses YOLO detections to auto-discover button positions
2. Cross-validates OCR regions against actual screen content
3. Maintains confidence scores per calibrated point
4. Self-heals when drift is detected (e.g., window resize)
5. Refuses to act when confidence is below threshold

Environment variables:
    TITAN_AUTOCALIB_ENABLED        ``1`` to enable (default on)
    TITAN_AUTOCALIB_MIN_CONFIDENCE  Minimum confidence to accept a calibration (0.0-1.0, default 0.70)
    TITAN_AUTOCALIB_HISTORY_SIZE    Number of frames to keep for consensus (default 10)
    TITAN_AUTOCALIB_DRIFT_PX        Max pixel drift before re-calibration (default 8)
    TITAN_AUTOCALIB_REPORT_DIR      Directory for calibration reports (default reports/)
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Constants ───────────────────────────────────────────────────────────────

BUTTON_ACTIONS = frozenset({
    "fold", "check", "call", "raise",
    "raise_2x", "raise_2_5x", "raise_pot",
    "raise_confirm", "allin",
})

REGION_LABELS = frozenset({"pot", "stack"})

ALL_CALIBRATABLE = BUTTON_ACTIONS | REGION_LABELS

# Default thresholds
DEFAULT_MIN_CONFIDENCE = 0.70
DEFAULT_HISTORY_SIZE = 10
DEFAULT_DRIFT_PX = 8
DEFAULT_REPORT_DIR = "reports"


# ── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class CalibrationPoint:
    """A single calibrated coordinate with confidence tracking."""
    label: str
    x: int
    y: int
    w: int = 0              # bounding box width (for regions)
    h: int = 0              # bounding box height (for regions)
    confidence: float = 0.0  # YOLO confidence
    frame_count: int = 0     # how many frames confirmed this position
    last_seen: float = 0.0   # timestamp of last confirmation
    stable: bool = False     # True when consensus reached

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "x": self.x, "y": self.y,
            "w": self.w, "h": self.h,
            "confidence": round(self.confidence, 4),
            "frame_count": self.frame_count,
            "last_seen": self.last_seen,
            "stable": self.stable,
        }


@dataclass
class CalibrationState:
    """Full calibration state for the session."""
    points: dict[str, CalibrationPoint] = field(default_factory=dict)
    history: dict[str, list[tuple[int, int, float]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    calibrated_at: str = ""
    resolution: tuple[int, int] = (720, 1280)
    drift_events: int = 0
    total_frames: int = 0

    def is_fully_calibrated(self, required: set[str] | None = None) -> bool:
        """Check if all required points are calibrated and stable."""
        if required is None:
            required = {"fold", "call", "raise"}
        return all(
            label in self.points and self.points[label].stable
            for label in required
        )

    def get_point(self, label: str) -> CalibrationPoint | None:
        return self.points.get(label)

    def confidence_score(self) -> float:
        """Overall calibration confidence (0.0-1.0)."""
        if not self.points:
            return 0.0
        stable_count = sum(1 for p in self.points.values() if p.stable)
        avg_conf = sum(p.confidence for p in self.points.values()) / len(self.points)
        coverage = stable_count / max(len(self.points), 1)
        return round(avg_conf * coverage, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibrated_at": self.calibrated_at,
            "resolution": list(self.resolution),
            "confidence_score": self.confidence_score(),
            "drift_events": self.drift_events,
            "total_frames": self.total_frames,
            "points": {k: v.to_dict() for k, v in self.points.items()},
        }


# ── Auto-Calibrator ────────────────────────────────────────────────────────

class AutoCalibrator:
    """Self-validating calibration engine.

    Usage::

        calibrator = AutoCalibrator()

        # Feed YOLO detections each frame
        calibrator.update(detections)

        # Get calibrated click point (returns None if not confident)
        point = calibrator.get_action_point("fold")
        if point is not None:
            click(point.x, point.y)

        # Check overall readiness
        if calibrator.is_ready():
            agent.start()
    """

    def __init__(
        self,
        min_confidence: float | None = None,
        history_size: int | None = None,
        drift_px: int | None = None,
        resolution: tuple[int, int] = (720, 1280),
        report_dir: str | None = None,
    ):
        self.min_confidence = min_confidence or float(
            os.environ.get("TITAN_AUTOCALIB_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE)
        )
        self.history_size = history_size or int(
            os.environ.get("TITAN_AUTOCALIB_HISTORY_SIZE", DEFAULT_HISTORY_SIZE)
        )
        self.drift_px = drift_px or int(
            os.environ.get("TITAN_AUTOCALIB_DRIFT_PX", DEFAULT_DRIFT_PX)
        )
        self.resolution = resolution
        self.report_dir = Path(
            report_dir or os.environ.get("TITAN_AUTOCALIB_REPORT_DIR", DEFAULT_REPORT_DIR)
        )
        self.state = CalibrationState(resolution=resolution)
        self._consensus_threshold = max(3, self.history_size // 3)

    # ── Public API ──────────────────────────────────────────────────────

    def update(self, detections: list[dict[str, Any]]) -> CalibrationState:
        """Feed one frame of YOLO detections to update calibration.

        Each detection dict must have:
            label: str, confidence: float, cx: int, cy: int, w: int, h: int

        Returns:
            Updated CalibrationState.
        """
        self.state.total_frames += 1
        now = time.time()

        for det in detections:
            label = det.get("label", "").strip().lower()
            if label not in ALL_CALIBRATABLE:
                continue

            conf = float(det.get("confidence", 0.0))
            if conf < self.min_confidence:
                continue

            cx = int(det.get("cx", 0))
            cy = int(det.get("cy", 0))
            w = int(det.get("w", 0))
            h = int(det.get("h", 0))

            # Validate coordinates are within screen bounds
            if not self._validate_coords(cx, cy):
                continue

            # Add to history
            history = self.state.history[label]
            history.append((cx, cy, conf))
            if len(history) > self.history_size:
                history.pop(0)

            # Check for drift against existing calibration
            existing = self.state.points.get(label)
            if existing and existing.stable:
                drift = abs(cx - existing.x) + abs(cy - existing.y)
                if drift > self.drift_px * 3:
                    # Major drift detected — reset this point
                    self.state.drift_events += 1
                    self.state.history[label] = [(cx, cy, conf)]
                    existing.stable = False
                    existing.frame_count = 0
                    continue

            # Compute consensus from history
            consensus = self._compute_consensus(label)
            if consensus is not None:
                con_x, con_y, con_conf, con_count = consensus
                point = CalibrationPoint(
                    label=label,
                    x=con_x, y=con_y,
                    w=w, h=h,
                    confidence=con_conf,
                    frame_count=con_count,
                    last_seen=now,
                    stable=con_count >= self._consensus_threshold,
                )
                self.state.points[label] = point

        self.state.calibrated_at = datetime.now(timezone.utc).isoformat()
        return self.state

    def get_action_point(self, label: str) -> CalibrationPoint | None:
        """Get a calibrated action point, only if stable and confident.

        Returns None if the point isn't ready — the agent should NOT
        click blindly.
        """
        point = self.state.points.get(label)
        if point is None:
            return None
        if not point.stable:
            return None
        if point.confidence < self.min_confidence:
            return None
        # Check staleness (if not seen for 30 seconds, mark unstable)
        if time.time() - point.last_seen > 30.0:
            point.stable = False
            return None
        return point

    def get_action_coords(self, label: str) -> tuple[int, int] | None:
        """Convenience: get (x, y) tuple or None."""
        point = self.get_action_point(label)
        if point is None:
            return None
        return (point.x, point.y)

    def is_ready(self, required: set[str] | None = None) -> bool:
        """Check if calibration is ready for gameplay."""
        return self.state.is_fully_calibrated(required)

    def confidence_score(self) -> float:
        """Overall calibration confidence."""
        return self.state.confidence_score()

    def get_status_report(self) -> dict[str, Any]:
        """Human-readable status for logging/HUD."""
        report: dict[str, Any] = {
            "ready": self.is_ready(),
            "confidence": self.confidence_score(),
            "total_frames": self.state.total_frames,
            "drift_events": self.state.drift_events,
            "points": {},
        }
        for label in sorted(ALL_CALIBRATABLE):
            point = self.state.points.get(label)
            if point:
                report["points"][label] = {
                    "pos": f"({point.x}, {point.y})",
                    "conf": f"{point.confidence:.2f}",
                    "stable": point.stable,
                    "frames": point.frame_count,
                }
            else:
                report["points"][label] = {"status": "not_detected"}
        return report

    def save_report(self, filename: str | None = None) -> Path:
        """Save calibration state to JSON file."""
        self.report_dir.mkdir(parents=True, exist_ok=True)
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"autocalib_{ts}.json"
        path = self.report_dir / filename
        data = {
            "version": 2,
            "type": "auto_calibration",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": {
                "min_confidence": self.min_confidence,
                "history_size": self.history_size,
                "drift_px": self.drift_px,
                "consensus_threshold": self._consensus_threshold,
            },
            "state": self.state.to_dict(),
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def load_from_file(self, path: Path | str) -> bool:
        """Restore calibration state from a previous report."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state_data = data.get("state", {})
            points_data = state_data.get("points", {})
            for label, pd in points_data.items():
                self.state.points[label] = CalibrationPoint(
                    label=pd["label"],
                    x=pd["x"], y=pd["y"],
                    w=pd.get("w", 0), h=pd.get("h", 0),
                    confidence=pd.get("confidence", 0.0),
                    frame_count=pd.get("frame_count", 0),
                    last_seen=pd.get("last_seen", time.time()),
                    stable=pd.get("stable", False),
                )
            return True
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    # ── Fallback: manual override ───────────────────────────────────────

    def set_manual_point(self, label: str, x: int, y: int, w: int = 0, h: int = 0) -> None:
        """Manually set a calibration point (e.g., from config_calibration.yaml)."""
        self.state.points[label] = CalibrationPoint(
            label=label,
            x=x, y=y, w=w, h=h,
            confidence=1.0,
            frame_count=999,
            last_seen=time.time(),
            stable=True,
        )

    def load_from_config(self, config: dict[str, Any]) -> int:
        """Load manual points from config_calibration.yaml action_buttons section.

        Returns number of points loaded.
        """
        action_buttons = config.get("action_buttons", {})
        count = 0
        for label, coords in action_buttons.items():
            if isinstance(coords, (list, tuple)) and len(coords) == 2:
                x, y = int(coords[0]), int(coords[1])
                self.set_manual_point(label, x, y)
                count += 1
        return count

    # ── Private Methods ─────────────────────────────────────────────────

    def _validate_coords(self, x: int, y: int) -> bool:
        """Check coordinates are within the expected resolution."""
        w, h = self.resolution
        return 0 <= x <= w and 0 <= y <= h

    def _compute_consensus(
        self, label: str
    ) -> tuple[int, int, float, int] | None:
        """Compute consensus position from history.

        Uses weighted median (by confidence) to be robust to outliers.
        Returns (x, y, avg_confidence, count) or None.
        """
        history = self.state.history.get(label, [])
        if len(history) < 2:
            return None

        xs = [h[0] for h in history]
        ys = [h[1] for h in history]
        confs = [h[2] for h in history]

        # Weighted median
        median_x = self._weighted_median(xs, confs)
        median_y = self._weighted_median(ys, confs)

        # Filter outliers (more than drift_px * 2 from median)
        filtered = [
            (x, y, c) for x, y, c in history
            if abs(x - median_x) <= self.drift_px * 2
            and abs(y - median_y) <= self.drift_px * 2
        ]

        if len(filtered) < 2:
            return None

        # Weighted average of filtered points
        total_w = sum(c for _, _, c in filtered)
        if total_w <= 0:
            return None

        avg_x = int(round(sum(x * c for x, _, c in filtered) / total_w))
        avg_y = int(round(sum(y * c for _, y, c in filtered) / total_w))
        avg_conf = total_w / len(filtered)

        return (avg_x, avg_y, avg_conf, len(filtered))

    @staticmethod
    def _weighted_median(values: list[int], weights: list[float]) -> int:
        """Compute weighted median."""
        pairs = sorted(zip(values, weights), key=lambda p: p[0])
        total = sum(weights)
        if total <= 0:
            return values[len(values) // 2] if values else 0
        cumsum = 0.0
        for val, w in pairs:
            cumsum += w
            if cumsum >= total / 2.0:
                return val
        return pairs[-1][0]


# ── OCR Region Auto-Calibrator ──────────────────────────────────────────────

class OCRRegionCalibrator:
    """Auto-calibrate OCR regions based on YOLO 'pot' and 'stack' detections.

    When YOLO detects a pot or stack region, this calibrator uses that
    bounding box to refine the OCR crop coordinates, eliminating manual
    tuning.
    """

    def __init__(self, padding_px: int = 4):
        self.padding = padding_px
        self.regions: dict[str, tuple[int, int, int, int]] = {}
        self._history: dict[str, list[tuple[int, int, int, int]]] = defaultdict(list)
        self._history_size = 8

    def update(self, label: str, x: int, y: int, w: int, h: int, confidence: float) -> None:
        """Update an OCR region from a YOLO detection."""
        if label not in REGION_LABELS and label not in {"pot", "stack", "call"}:
            return
        if confidence < 0.5:
            return

        # Add padding for OCR margin
        padded = (
            max(0, x - self.padding),
            max(0, y - self.padding),
            w + self.padding * 2,
            h + self.padding * 2,
        )
        self._history[label].append(padded)
        if len(self._history[label]) > self._history_size:
            self._history[label].pop(0)

        # Compute stable region from history
        if len(self._history[label]) >= 3:
            all_x = [r[0] for r in self._history[label]]
            all_y = [r[1] for r in self._history[label]]
            all_w = [r[2] for r in self._history[label]]
            all_h = [r[3] for r in self._history[label]]

            self.regions[label] = (
                int(sorted(all_x)[len(all_x) // 2]),  # median
                int(sorted(all_y)[len(all_y) // 2]),
                int(sorted(all_w)[len(all_w) // 2]),
                int(sorted(all_h)[len(all_h) // 2]),
            )

    def get_region(self, label: str) -> tuple[int, int, int, int] | None:
        """Get calibrated OCR region as (x, y, w, h) or None."""
        return self.regions.get(label)

    def get_region_str(self, label: str) -> str | None:
        """Get region as 'x,y,w,h' string (config format)."""
        r = self.get_region(label)
        if r is None:
            return None
        return f"{r[0]},{r[1]},{r[2]},{r[3]}"

    def to_dict(self) -> dict[str, str]:
        """Export all calibrated regions as config-format strings."""
        return {label: self.get_region_str(label) or "" for label in self.regions}


# ── Confidence Gate ─────────────────────────────────────────────────────────

class ConfidenceGate:
    """Decision gate that prevents actions when detection confidence is low.

    The agent must pass all detections through this gate before acting.
    If confidence is insufficient, the gate returns WAIT instead of
    an unreliable action.
    """

    def __init__(
        self,
        card_threshold: float = 0.45,
        button_threshold: float = 0.60,
        ocr_threshold: float = 0.40,
        min_cards_for_decision: int = 4,
    ):
        self.card_threshold = card_threshold
        self.button_threshold = button_threshold
        self.ocr_threshold = ocr_threshold
        self.min_cards_for_decision = min_cards_for_decision
        self._gate_log: list[dict[str, Any]] = []

    def check_cards(self, card_detections: list[dict[str, Any]]) -> tuple[bool, str]:
        """Check if card detections are reliable enough for a decision.

        Returns:
            (passed, reason) — passed=True if cards are reliable.
        """
        if not card_detections:
            return False, "no_cards_detected"

        above_threshold = [
            d for d in card_detections
            if d.get("confidence", 0) >= self.card_threshold
        ]

        if len(above_threshold) < self.min_cards_for_decision:
            return False, f"insufficient_confident_cards({len(above_threshold)}/{self.min_cards_for_decision})"

        # Check for duplicate card detections (impossible in real poker)
        labels = [d["label"] for d in above_threshold]
        unique_labels = set(labels)
        if len(unique_labels) < len(labels):
            duplicates = [l for l in unique_labels if labels.count(l) > 1]
            return False, f"duplicate_cards_detected({duplicates})"

        return True, "ok"

    def check_buttons(self, button_detections: list[dict[str, Any]]) -> tuple[bool, str]:
        """Check if at least one action button is confidently detected."""
        above_threshold = [
            d for d in button_detections
            if d.get("confidence", 0) >= self.button_threshold
        ]
        if not above_threshold:
            return False, "no_confident_buttons"
        return True, "ok"

    def check_ocr(
        self,
        pot_value: float | None,
        stack_value: float | None,
        call_value: float | None,
    ) -> tuple[bool, str]:
        """Check if OCR values are sane."""
        issues: list[str] = []

        if pot_value is not None and pot_value < 0:
            issues.append("negative_pot")
        if stack_value is not None and stack_value < 0:
            issues.append("negative_stack")
        if call_value is not None and call_value < 0:
            issues.append("negative_call")

        # Call can't exceed stack
        if (call_value is not None and stack_value is not None
                and call_value > stack_value * 1.1):
            issues.append("call_exceeds_stack")

        # Pot can't be zero if call > 0
        if (pot_value is not None and call_value is not None
                and pot_value == 0 and call_value > 0):
            issues.append("zero_pot_with_call")

        if issues:
            return False, f"ocr_sanity_fail({','.join(issues)})"
        return True, "ok"

    def full_check(
        self,
        card_detections: list[dict[str, Any]],
        button_detections: list[dict[str, Any]],
        pot: float | None = None,
        stack: float | None = None,
        call_amount: float | None = None,
    ) -> tuple[bool, list[str]]:
        """Run all confidence checks.

        Returns:
            (all_passed, list_of_reasons)
        """
        results: list[tuple[bool, str]] = []

        results.append(self.check_cards(card_detections))
        results.append(self.check_buttons(button_detections))
        results.append(self.check_ocr(pot, stack, call_amount))

        all_passed = all(r[0] for r in results)
        reasons = [r[1] for r in results if not r[0]]

        self._gate_log.append({
            "timestamp": time.time(),
            "passed": all_passed,
            "reasons": reasons,
        })

        # Keep log bounded
        if len(self._gate_log) > 100:
            self._gate_log = self._gate_log[-50:]

        return all_passed, reasons

    def pass_rate(self, last_n: int = 20) -> float:
        """Fraction of recent checks that passed (0.0-1.0)."""
        recent = self._gate_log[-last_n:]
        if not recent:
            return 0.0
        return sum(1 for r in recent if r["passed"]) / len(recent)

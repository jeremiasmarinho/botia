"""Runtime configuration dataclasses for server, agent and vision.

Each dataclass reads its defaults from environment variables at
construction time.  Override individual fields when constructing
from code (e.g. in tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os


@dataclass(slots=True)
class ServerConfig:
    """HiveBrain server configuration."""

    zmq_bind: str = field(default_factory=lambda: os.getenv("TITAN_ZMQ_BIND", "tcp://0.0.0.0:5555"))
    redis_url: str = field(default_factory=lambda: os.getenv("TITAN_REDIS_URL", "redis://:titan_secret@127.0.0.1:6379/0"))


@dataclass(slots=True)
class AgentRuntimeConfig:
    """Agent-level runtime configuration."""

    agent_id: str = field(default_factory=lambda: os.getenv("TITAN_AGENT_ID", "01"))
    zmq_server: str = field(default_factory=lambda: os.getenv("TITAN_ZMQ_SERVER", "tcp://127.0.0.1:5555"))
    table_id: str = field(default_factory=lambda: os.getenv("TITAN_TABLE_ID", "table_default"))
    heartbeat_seconds: float = field(default_factory=lambda: float(os.getenv("TITAN_AGENT_HEARTBEAT", "1.0")))
    timeout_ms: int = field(default_factory=lambda: int(os.getenv("TITAN_AGENT_TIMEOUT_MS", "1500")))


@dataclass(slots=True)
class VisionRuntimeConfig:
    """Vision subsystem configuration (monitor region + model path).

    NOTE: All fields use ``default_factory`` so that environment variables
    are read at **instantiation** time, not at import/class-definition time.
    This ensures ``monkeypatch.setenv`` in tests works correctly.
    """

    model_path: str = field(default_factory=lambda: os.getenv("TITAN_YOLO_MODEL", ""))
    monitor_left: int = field(default_factory=lambda: int(os.getenv("TITAN_MONITOR_LEFT", "0")))
    monitor_top: int = field(default_factory=lambda: int(os.getenv("TITAN_MONITOR_TOP", "0")))
    monitor_width: int = field(default_factory=lambda: int(os.getenv("TITAN_MONITOR_WIDTH", "0")))
    monitor_height: int = field(default_factory=lambda: int(os.getenv("TITAN_MONITOR_HEIGHT", "0")))

    def monitor_region(self) -> dict[str, int] | None:
        """Return an ``mss``-compatible monitor dict, or ``None`` for full screen."""
        if self.monitor_width <= 0 or self.monitor_height <= 0:
            return None
        return {
            "left": self.monitor_left,
            "top": self.monitor_top,
            "width": self.monitor_width,
            "height": self.monitor_height,
        }


@dataclass(slots=True)
class OCRRuntimeConfig:
    """OCR subsystem configuration (regions + backend toggles).

    Regions are relative to the emulator game canvas (ROI) used by
    ``VisionYolo.capture_frame()``.
    """

    enabled: bool = field(default_factory=lambda: os.getenv("TITAN_OCR_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"})
    use_easyocr: bool = field(default_factory=lambda: os.getenv("TITAN_OCR_USE_EASYOCR", "0").strip().lower() in {"1", "true", "yes", "on"})
    tesseract_cmd: str = field(default_factory=lambda: os.getenv("TITAN_TESSERACT_CMD", "").strip())

    # default ROIs (x, y, w, h) relative to emulator canvas
    pot_region: str = field(default_factory=lambda: os.getenv("TITAN_OCR_POT_REGION", "360,255,180,54"))
    stack_region: str = field(default_factory=lambda: os.getenv("TITAN_OCR_STACK_REGION", "330,610,220,56"))
    call_region: str = field(default_factory=lambda: os.getenv("TITAN_OCR_CALL_REGION", "450,690,180,54"))
    regions_file: str = field(default_factory=lambda: os.getenv("TITAN_OCR_REGIONS_FILE", os.path.join("reports", "ocr_regions_latest.json")).strip())
    regions_json: str = field(default_factory=lambda: os.getenv("TITAN_OCR_REGIONS_JSON", "").strip())

    pot_min: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_POT_MIN", "0")))
    pot_max: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_POT_MAX", "200000")))
    stack_min: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_STACK_MIN", "0")))
    stack_max: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_STACK_MAX", "500000")))
    call_min: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_CALL_MIN", "0")))
    call_max: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_CALL_MAX", "50000")))

    pot_max_delta: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_POT_MAX_DELTA", "25000")))
    stack_max_delta: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_STACK_MAX_DELTA", "10000")))
    call_max_delta: float = field(default_factory=lambda: float(os.getenv("TITAN_OCR_CALL_MAX_DELTA", "5000")))

    @staticmethod
    def _parse_region(value: str) -> tuple[int, int, int, int] | None:
        raw = (value or "").strip().replace(" ", "")
        if not raw:
            return None
        parts = raw.split(",")
        if len(parts) != 4:
            return None
        try:
            x, y, w, h = (int(part) for part in parts)
        except ValueError:
            return None
        if w <= 0 or h <= 0:
            return None
        return (max(0, x), max(0, y), w, h)

    @staticmethod
    def _merge_region_payload(
        regions: dict[str, tuple[int, int, int, int]],
        payload: dict[str, object],
    ) -> dict[str, tuple[int, int, int, int]]:
        for key in ("pot", "hero_stack", "call_amount"):
            raw_region = payload.get(key)
            if isinstance(raw_region, list) and len(raw_region) == 4:
                try:
                    x, y, w, h = (int(v) for v in raw_region)
                except (TypeError, ValueError):
                    continue
                if w > 0 and h > 0:
                    regions[key] = (max(0, x), max(0, y), w, h)
        return regions

    def value_limits(self) -> dict[str, tuple[float, float]]:
        return {
            "pot": (float(self.pot_min), float(self.pot_max)),
            "hero_stack": (float(self.stack_min), float(self.stack_max)),
            "call_amount": (float(self.call_min), float(self.call_max)),
        }

    def max_deltas(self) -> dict[str, float]:
        return {
            "pot": float(self.pot_max_delta),
            "hero_stack": float(self.stack_max_delta),
            "call_amount": float(self.call_max_delta),
        }

    def regions(self) -> dict[str, tuple[int, int, int, int]]:
        """Return effective OCR regions keyed by metric name."""
        default_regions = {
            "pot": (360, 255, 180, 54),
            "hero_stack": (330, 610, 220, 56),
            "call_amount": (450, 690, 180, 54),
        }
        resolved_regions = dict(default_regions)

        regions_file = (self.regions_file or "").strip()
        if regions_file and os.path.exists(regions_file):
            try:
                with open(regions_file, "r", encoding="utf-8") as file_stream:
                    payload = json.load(file_stream)
                if isinstance(payload, dict):
                    resolved_regions = self._merge_region_payload(resolved_regions, payload)
            except Exception:
                pass

        env_regions = {
            "pot": self._parse_region(self.pot_region),
            "hero_stack": self._parse_region(self.stack_region),
            "call_amount": self._parse_region(self.call_region),
        }
        for key, region in env_regions.items():
            if region is not None:
                resolved_regions[key] = region

        if not self.regions_json:
            return resolved_regions

        try:
            payload = json.loads(self.regions_json)
        except Exception:
            return resolved_regions

        if not isinstance(payload, dict):
            return resolved_regions

        return self._merge_region_payload(resolved_regions, payload)

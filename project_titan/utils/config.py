"""Runtime configuration dataclasses for server, agent and vision.

Each dataclass reads its defaults from environment variables at
construction time.  Override individual fields when constructing
from code (e.g. in tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os


@dataclass(slots=True)
class ServerConfig:
    """HiveBrain server configuration."""

    zmq_bind: str = field(default_factory=lambda: os.getenv("TITAN_ZMQ_BIND", "tcp://0.0.0.0:5555"))
    redis_url: str = field(default_factory=lambda: os.getenv("TITAN_REDIS_URL", "redis://127.0.0.1:6379/0"))


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
    """Vision subsystem configuration (monitor region + model path)."""

    model_path: str = os.getenv("TITAN_YOLO_MODEL", "")
    monitor_left: int = int(os.getenv("TITAN_MONITOR_LEFT", "0"))
    monitor_top: int = int(os.getenv("TITAN_MONITOR_TOP", "0"))
    monitor_width: int = int(os.getenv("TITAN_MONITOR_WIDTH", "0"))
    monitor_height: int = int(os.getenv("TITAN_MONITOR_HEIGHT", "0"))

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

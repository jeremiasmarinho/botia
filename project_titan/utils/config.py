from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(slots=True)
class ServerConfig:
    zmq_bind: str = "tcp://0.0.0.0:5555"
    redis_url: str = "redis://127.0.0.1:6379/0"


@dataclass(slots=True)
class AgentRuntimeConfig:
    agent_id: str = "01"
    zmq_server: str = "tcp://127.0.0.1:5555"


@dataclass(slots=True)
class VisionRuntimeConfig:
    model_path: str = os.getenv("TITAN_YOLO_MODEL", "")
    monitor_left: int = int(os.getenv("TITAN_MONITOR_LEFT", "0"))
    monitor_top: int = int(os.getenv("TITAN_MONITOR_TOP", "0"))
    monitor_width: int = int(os.getenv("TITAN_MONITOR_WIDTH", "0"))
    monitor_height: int = int(os.getenv("TITAN_MONITOR_HEIGHT", "0"))

    def monitor_region(self) -> dict[str, int] | None:
        if self.monitor_width <= 0 or self.monitor_height <= 0:
            return None
        return {
            "left": self.monitor_left,
            "top": self.monitor_top,
            "width": self.monitor_width,
            "height": self.monitor_height,
        }

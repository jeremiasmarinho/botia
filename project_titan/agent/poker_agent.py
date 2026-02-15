from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from utils.config import AgentRuntimeConfig

try:
    import zmq
except Exception:
    zmq = None


@dataclass(slots=True)
class AgentConfig:
    agent_id: str
    server_address: str
    table_id: str = "table_default"
    interval_seconds: float = 1.0
    timeout_ms: int = 1500


class PokerAgent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._context: Any | None = None
        self._socket: Any | None = None

    def _connect(self) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq não disponível. Instale dependências com requirements.txt")

        if self._context is None:
            self._context = zmq.Context.instance()

        if self._socket is not None:
            try:
                self._socket.close(0)
            except Exception:
                pass

        socket = self._context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, max(100, int(self.config.timeout_ms)))
        socket.setsockopt(zmq.SNDTIMEO, max(100, int(self.config.timeout_ms)))
        socket.connect(self.config.server_address)
        self._socket = socket

    @staticmethod
    def _sample_cards() -> list[str]:
        return ["As", "Kd", "Qh", "Js", "Tc", "9d"]

    def _checkin(self) -> dict[str, Any]:
        if self._socket is None:
            self._connect()

        payload = {
            "type": "checkin",
            "agent_id": self.config.agent_id,
            "table_id": self.config.table_id,
            "cards": self._sample_cards(),
        }

        try:
            self._socket.send_json(payload)
            response = self._socket.recv_json()
            if isinstance(response, dict):
                return response
            return {"ok": False, "error": "invalid_response"}
        except Exception:
            self._connect()
            return {"ok": False, "error": "connection_timeout"}

    def run(self) -> None:
        self._connect()
        print(
            f"[Agent {self.config.agent_id}] connected to {self.config.server_address} "
            f"table={self.config.table_id}"
        )
        while True:
            response = self._checkin()
            mode = response.get("mode", "unknown")
            partners = response.get("partners", [])
            dead_cards = response.get("dead_cards", [])
            latency_ms = response.get("latency_ms", "-")
            print(
                f"[Agent {self.config.agent_id}] mode={mode} partners={partners} "
                f"dead_cards={dead_cards} latency_ms={latency_ms}"
            )
            time.sleep(max(0.1, float(self.config.interval_seconds)))


if __name__ == "__main__":
    runtime = AgentRuntimeConfig()
    PokerAgent(
        AgentConfig(
            agent_id=runtime.agent_id,
            server_address=runtime.zmq_server,
            table_id=runtime.table_id,
            interval_seconds=runtime.heartbeat_seconds,
            timeout_ms=runtime.timeout_ms,
        )
    ).run()

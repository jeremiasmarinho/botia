from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AgentSession:
    agent_id: str
    table_id: str
    updated_at: float
    payload: dict[str, Any]


class HiveBrain:
    def __init__(self, bind_address: str) -> None:
        self.bind_address = bind_address
        self.sessions: dict[str, AgentSession] = {}

    def start(self) -> None:
        print(f"[HiveBrain] Listening on {self.bind_address}")
        print("[HiveBrain] Skeleton server initialized")

    def register_agent(self, agent_id: str, table_id: str, payload: dict[str, Any]) -> None:
        self.sessions[agent_id] = AgentSession(
            agent_id=agent_id,
            table_id=table_id,
            updated_at=time.time(),
            payload=payload,
        )


if __name__ == "__main__":
    server = HiveBrain("tcp://0.0.0.0:5555")
    server.start()

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class AgentConfig:
    agent_id: str
    server_address: str


class PokerAgent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def run(self) -> None:
        print(f"[Agent {self.config.agent_id}] connected to {self.config.server_address}")
        while True:
            time.sleep(1)
            print(f"[Agent {self.config.agent_id}] heartbeat")


if __name__ == "__main__":
    PokerAgent(AgentConfig(agent_id="01", server_address="tcp://127.0.0.1:5555")).run()

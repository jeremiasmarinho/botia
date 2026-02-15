from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from memory.redis_memory import RedisMemory
from orchestrator.registry import ServiceRegistry
from workflows.poker_hand_workflow import PokerHandWorkflow
from tools.vision_tool import VisionTool
from tools.equity_tool import EquityTool
from tools.action_tool import ActionTool
from agents.zombie_agent import ZombieAgent
from utils.config import ServerConfig, VisionRuntimeConfig


@dataclass(slots=True)
class EngineConfig:
    tick_seconds: float = 0.2
    max_ticks: int | None = None


class Orchestrator:
    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.registry = ServiceRegistry()
        self._running = False

    def bootstrap(self) -> None:
        server_config = ServerConfig()
        vision_config = VisionRuntimeConfig()
        self.registry.memory = RedisMemory(redis_url=server_config.redis_url, ttl_seconds=5)

        vision_tool = VisionTool(
            model_path=vision_config.model_path,
            monitor=vision_config.monitor_region(),
        )
        equity_tool = EquityTool()
        action_tool = ActionTool()
        workflow = PokerHandWorkflow(vision_tool, equity_tool, action_tool, self.registry.memory)
        agent = ZombieAgent(workflow)

        print(f"[Orchestrator] memory backend={self.registry.memory.backend}")

        self.registry.register_tool("vision", vision_tool)
        self.registry.register_tool("equity", equity_tool)
        self.registry.register_tool("action", action_tool)
        self.registry.register_workflow("poker_hand", workflow)
        self.registry.register_agent("zombie_01", agent)

    def run(self) -> None:
        self.bootstrap()
        self._running = True
        print("[Orchestrator] running composition loop")
        tick_count = 0

        while self._running:
            for agent_name, agent in self.registry.agents.items():
                outcome = agent.step()
                if outcome is not None:
                    print(f"[Orchestrator] {agent_name}: {outcome}")
            tick_count += 1
            if self.config.max_ticks is not None and tick_count >= self.config.max_ticks:
                print(f"[Orchestrator] reached max ticks={self.config.max_ticks}. stopping loop")
                self.stop()
                break
            time.sleep(self.config.tick_seconds)

    def stop(self) -> None:
        self._running = False


def main() -> None:
    max_ticks_raw = os.getenv("TITAN_MAX_TICKS", "").strip()
    max_ticks = int(max_ticks_raw) if max_ticks_raw.isdigit() else None
    Orchestrator(config=EngineConfig(max_ticks=max_ticks)).run()


if __name__ == "__main__":
    main()

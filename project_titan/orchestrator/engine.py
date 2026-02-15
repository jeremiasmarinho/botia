from __future__ import annotations

import json
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

    @staticmethod
    def _parse_outcome_metrics(outcome: str) -> tuple[str | None, float | None]:
        action: str | None = None
        win_rate: float | None = None

        for token in outcome.split():
            if token.startswith("action="):
                value = token.split("=", 1)[1].strip()
                if value:
                    action = value
                continue

            if token.startswith("win_rate="):
                raw_value = token.split("=", 1)[1].strip()
                try:
                    win_rate = float(raw_value)
                except ValueError:
                    win_rate = None

        return action, win_rate

    @staticmethod
    def _write_report_file(report: dict[str, Any]) -> str | None:
        report_dir = os.getenv("TITAN_REPORT_DIR", "").strip()
        if not report_dir:
            return None

        try:
            os.makedirs(report_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"run_report_{timestamp}.json"
            file_path = os.path.join(report_dir, filename)
            with open(file_path, "w", encoding="utf-8") as report_file:
                json.dump(report, report_file, ensure_ascii=False, indent=2)
            return file_path
        except OSError as error:
            print(f"[Orchestrator] report_write_error={error}")
            return None

    def run(self) -> None:
        self.bootstrap()
        self._running = True
        print("[Orchestrator] running composition loop")
        tick_count = 0
        total_outcomes = 0
        action_counts: dict[str, int] = {}
        win_rate_sum = 0.0
        win_rate_count = 0
        started_at = time.perf_counter()

        try:
            while self._running:
                for agent_name, agent in self.registry.agents.items():
                    outcome = agent.step()
                    if outcome is not None:
                        total_outcomes += 1
                        print(f"[Orchestrator] {agent_name}: {outcome}")

                        action, win_rate = self._parse_outcome_metrics(outcome)
                        if action is not None:
                            action_counts[action] = action_counts.get(action, 0) + 1

                        if win_rate is not None:
                            win_rate_sum += win_rate
                            win_rate_count += 1

                tick_count += 1
                if self.config.max_ticks is not None and tick_count >= self.config.max_ticks:
                    print(f"[Orchestrator] reached max ticks={self.config.max_ticks}. stopping loop")
                    self.stop()
                    break
                time.sleep(self.config.tick_seconds)
        except KeyboardInterrupt:
            print("[Orchestrator] interrupted by user. stopping loop")
            self.stop()
        finally:
            duration_seconds = time.perf_counter() - started_at
            average_win_rate = None
            if win_rate_count > 0:
                average_win_rate = round(win_rate_sum / win_rate_count, 4)

            report = {
                "ticks": tick_count,
                "outcomes": total_outcomes,
                "average_win_rate": average_win_rate,
                "action_counts": action_counts,
                "duration_seconds": round(duration_seconds, 3),
            }
            print(f"[Orchestrator] run_report={json.dumps(report, ensure_ascii=False)}")
            report_file = self._write_report_file(report)
            if report_file is not None:
                print(f"[Orchestrator] run_report_file={report_file}")

    def stop(self) -> None:
        self._running = False


def main() -> None:
    max_ticks_raw = os.getenv("TITAN_MAX_TICKS", "").strip()
    max_ticks = int(max_ticks_raw) if max_ticks_raw.isdigit() else None
    tick_seconds_raw = os.getenv("TITAN_TICK_SECONDS", "").strip()
    try:
        tick_seconds = float(tick_seconds_raw) if tick_seconds_raw else 0.2
    except ValueError:
        tick_seconds = 0.2

    if tick_seconds <= 0:
        tick_seconds = 0.2

    Orchestrator(config=EngineConfig(tick_seconds=tick_seconds, max_ticks=max_ticks)).run()


if __name__ == "__main__":
    main()

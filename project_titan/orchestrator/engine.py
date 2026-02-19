"""Orchestrator — composition loop that drives agents each tick.

Bootstraps all tools, workflows and agents via :class:`ServiceRegistry`,
then enters a loop that calls ``agent.step()`` on every registered agent.
Accumulates telemetry (action counts, win rates, simulation usage, RNG
watchdog) and writes a JSON report on exit.

Environment variables
---------------------
``TITAN_MAX_TICKS``     Maximum loop iterations (``None`` = infinite).
``TITAN_TICK_SECONDS``  Sleep between ticks (default ``0.2``).
``TITAN_REPORT_DIR``    Directory for JSON run reports.
"""

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
from tools.rng_tool import RngTool
from agent.zombie_agent import ZombieAgent
from utils.config import ServerConfig, VisionRuntimeConfig
from utils.logger import TitanLogger

_log = TitanLogger("Orchestrator")


@dataclass(slots=True)
class EngineConfig:
    """Orchestrator loop settings.

    Attributes:
        tick_seconds: Sleep duration between ticks.
        max_ticks:    Stop after this many ticks (``None`` = run forever).
    """

    tick_seconds: float = 0.2
    max_ticks: int | None = None


class Orchestrator:
    """Top-level composition engine."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.registry = ServiceRegistry()
        self._running = False

    def bootstrap(self) -> None:
        """Wire up all services (memory, tools, workflow, agent) into the registry."""
        server_config = ServerConfig()
        vision_config = VisionRuntimeConfig()
        self.registry.memory = RedisMemory(redis_url=server_config.redis_url, ttl_seconds=5)

        vision_tool = VisionTool(
            model_path=vision_config.model_path,
            monitor=vision_config.monitor_region(),
        )
        equity_tool = EquityTool()
        action_tool = ActionTool()
        rng_tool = RngTool(storage=self.registry.memory)
        workflow = PokerHandWorkflow(vision_tool, equity_tool, action_tool, self.registry.memory, rng_tool)
        agent = ZombieAgent(workflow)

        _log.info(f"memory backend={self.registry.memory.backend}")

        self.registry.register_tool("vision", vision_tool)
        self.registry.register_tool("equity", equity_tool)
        self.registry.register_tool("action", action_tool)
        self.registry.register_tool("rng", rng_tool)
        self.registry.register_workflow("poker_hand", workflow)
        self.registry.register_agent("zombie_01", agent)

    @staticmethod
    def _parse_outcome_metrics(outcome: Any) -> tuple[str | None, float | None]:
        """Extract ``action`` and ``win_rate`` from string or Decision payloads."""
        action: str | None = None
        win_rate: float | None = None

        if hasattr(outcome, "action"):
            raw_action = getattr(outcome, "action", None)
            if isinstance(raw_action, str) and raw_action.strip():
                action = raw_action.strip().lower()

            raw_equity = getattr(outcome, "equity", None)
            if isinstance(raw_equity, (int, float)):
                win_rate = float(raw_equity)
            elif isinstance(raw_equity, str):
                try:
                    win_rate = float(raw_equity.strip())
                except ValueError:
                    win_rate = None

            return action, win_rate

        if not isinstance(outcome, str):
            return None, None

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
    def _format_outcome_for_log(outcome: Any) -> str:
        """Render workflow outcome for console logs."""
        if hasattr(outcome, "action"):
            action = getattr(outcome, "action", "wait")
            amount = getattr(outcome, "amount", 0.0)
            equity = getattr(outcome, "equity", 0.0)
            spr = getattr(outcome, "spr", 99.0)
            mode = getattr(outcome, "mode", "SOLO")
            committed = getattr(outcome, "committed", False)
            description = getattr(outcome, "description", "")
            return (
                f"action={action} amount={amount} equity={equity:.4f} "
                f"spr={spr} mode={mode} committed={committed} desc={description}"
            )
        return str(outcome)

    @staticmethod
    def _extract_simulations_from_decision(last_decision: Any) -> tuple[int | None, bool | None]:
        """Return ``(simulation_count, dynamic_enabled)`` from a decision dict."""
        if not isinstance(last_decision, dict):
            return None, None

        simulations = last_decision.get("simulations")
        dynamic_simulations = last_decision.get("dynamic_simulations")

        parsed_simulations: int | None = None
        if isinstance(simulations, int):
            parsed_simulations = simulations
        elif isinstance(simulations, str) and simulations.strip().isdigit():
            parsed_simulations = int(simulations.strip())

        parsed_dynamic: bool | None = None
        if isinstance(dynamic_simulations, bool):
            parsed_dynamic = dynamic_simulations
        elif isinstance(dynamic_simulations, str):
            parsed_dynamic = dynamic_simulations.strip().lower() in {"1", "true", "yes", "on"}

        return parsed_simulations, parsed_dynamic

    @staticmethod
    def _write_report_file(report: dict[str, Any]) -> str | None:
        """Persist *report* as JSON under ``TITAN_REPORT_DIR``. Return path or ``None``."""
        report_dir = os.getenv("TITAN_REPORT_DIR", "").strip()
        if not report_dir:
            return None

        try:
            os.makedirs(report_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            millis = int((time.time() % 1) * 1000)
            filename = f"run_report_{timestamp}_{millis:03d}.json"
            file_path = os.path.join(report_dir, filename)
            with open(file_path, "w", encoding="utf-8") as report_file:
                json.dump(report, report_file, ensure_ascii=False, indent=2)
            return file_path
        except OSError as error:
            _log.error(f"report_write_error={error}")
            return None

    def run(self) -> None:
        """Bootstrap, enter the tick loop, and write a report on exit."""
        self.bootstrap()
        self._running = True
        _log.highlight("running composition loop")
        tick_count = 0
        total_outcomes = 0
        action_counts: dict[str, int] = {}
        win_rate_sum = 0.0
        win_rate_count = 0
        simulations_sum = 0
        simulations_count = 0
        simulations_min: int | None = None
        simulations_max: int | None = None
        dynamic_simulation_decisions = 0
        started_at = time.perf_counter()

        try:
            while self._running:
                for agent_name, agent in self.registry.agents.items():
                    outcome = agent.step()
                    if outcome is not None:
                        total_outcomes += 1
                        _log.status(f"{agent_name}: {self._format_outcome_for_log(outcome)}")

                        action, win_rate = self._parse_outcome_metrics(outcome)
                        if action is not None:
                            action_counts[action] = action_counts.get(action, 0) + 1

                        if win_rate is not None:
                            win_rate_sum += win_rate
                            win_rate_count += 1

                        last_decision = self.registry.memory.get("last_decision", {})
                        simulations_value, dynamic_enabled = self._extract_simulations_from_decision(last_decision)
                        if simulations_value is not None and simulations_value > 0:
                            simulations_sum += simulations_value
                            simulations_count += 1
                            simulations_min = (
                                simulations_value if simulations_min is None else min(simulations_min, simulations_value)
                            )
                            simulations_max = (
                                simulations_value if simulations_max is None else max(simulations_max, simulations_value)
                            )

                        if dynamic_enabled is True:
                            dynamic_simulation_decisions += 1

                tick_count += 1
                if self.config.max_ticks is not None and tick_count >= self.config.max_ticks:
                    _log.info(f"reached max ticks={self.config.max_ticks}. stopping loop")
                    self.stop()
                    break
                time.sleep(self.config.tick_seconds)
        except KeyboardInterrupt:
            _log.warn("interrupted by user. stopping loop")
            self.stop()
        finally:
            duration_seconds = time.perf_counter() - started_at
            average_win_rate = None
            if win_rate_count > 0:
                average_win_rate = round(win_rate_sum / win_rate_count, 4)

            simulation_usage: dict[str, Any] = {
                "count": simulations_count,
                "average": None,
                "min": simulations_min,
                "max": simulations_max,
                "dynamic_enabled_decisions": dynamic_simulation_decisions,
            }
            if simulations_count > 0:
                simulation_usage["average"] = round(simulations_sum / simulations_count, 2)

            rng_watchdog: dict[str, Any] = {
                "players_audited": 0,
                "players_flagged": 0,
                "flagged_opponents": [],
                "top_zscores": [],
            }
            rng_tool = self.registry.tools.get("rng")
            if rng_tool is not None and hasattr(rng_tool, "telemetry_summary"):
                try:
                    rng_summary = rng_tool.telemetry_summary(top_k=3)
                    if isinstance(rng_summary, dict):
                        rng_watchdog = rng_summary
                except Exception as error:
                    rng_watchdog = {
                        "players_audited": 0,
                        "players_flagged": 0,
                        "flagged_opponents": [],
                        "top_zscores": [],
                        "error": str(error),
                    }

            report = {
                "ticks": tick_count,
                "outcomes": total_outcomes,
                "average_win_rate": average_win_rate,
                "action_counts": action_counts,
                "simulation_usage": simulation_usage,
                "rng_watchdog": rng_watchdog,
                "duration_seconds": round(duration_seconds, 3),
            }
            _log.success(f"run_report={json.dumps(report, ensure_ascii=False)}")
            report_file = self._write_report_file(report)
            if report_file is not None:
                _log.info(f"run_report_file={report_file}")

    def stop(self) -> None:
        """Signal the tick loop to exit after the current iteration."""
        self._running = False


def main() -> None:
    """CLI entry-point: read env vars and start the orchestrator."""
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

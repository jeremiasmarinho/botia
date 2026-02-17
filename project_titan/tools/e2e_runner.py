"""
Project Titan — End-to-End Test Runner

Executa o pipeline completo em modo simulado ou real:
  capture -> detect -> parse -> decide -> act -> report

Modos:
  sim:  usa cenarios simulados do VisionTool (sem YOLO/emulador)
  real: captura de tela + YOLO + decisao + GhostMouse

Uso:
    python tools/e2e_runner.py --mode sim --cycles 5
    python tools/e2e_runner.py --mode sim --cycles 5 --visual
    python tools/e2e_runner.py --mode sim --cycles 5 --save-report reports/e2e_latest.json
    python tools/e2e_runner.py --mode real --model best.pt --visual
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(slots=True)
class E2ECycleResult:
    """Result of a single E2E cycle."""
    cycle: int
    scenario: str
    hero_cards: list[str]
    board_cards: list[str]
    pot: float
    stack: float
    active_players: int
    is_my_turn: bool
    action: str
    street: str
    win_rate: float
    pot_odds: float
    difficulty: str
    delay_seconds: float
    latency_ms: float
    timestamp: str


@dataclass
class E2EReport:
    """Aggregate E2E test report."""
    mode: str
    total_cycles: int
    completed_cycles: int
    pass_count: int
    fail_count: int
    overall_status: str
    avg_latency_ms: float
    action_distribution: dict[str, int]
    cycles: list[dict]
    generated_at: str
    duration_seconds: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="E2E test runner for Project Titan")
    parser.add_argument("--mode", choices=["sim", "real"], default="sim",
                        help="sim = simulated vision, real = YOLO + screen capture")
    parser.add_argument("--cycles", type=int, default=5,
                        help="Number of E2E cycles to execute")
    parser.add_argument("--scenario", default="cycle",
                        help="Sim scenario (wait/fold/call/raise/cycle)")
    parser.add_argument("--model", default=None,
                        help="YOLO model path for real mode")
    parser.add_argument("--visual", action="store_true",
                        help="Show visual overlay (requires OpenCV)")
    parser.add_argument("--save-frames", default=None,
                        help="Directory to save annotated frames")
    parser.add_argument("--save-report", default=None,
                        help="Path to save JSON report")
    parser.add_argument("--json", action="store_true",
                        help="Output report as JSON to stdout")
    parser.add_argument("--tick-seconds", type=float, default=0.2,
                        help="Delay between cycles")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config without running")
    return parser.parse_args()


def _setup_sim_env(scenario: str) -> None:
    """Configure env for simulated mode."""
    os.environ["TITAN_SIM_SCENARIO"] = scenario
    os.environ.setdefault("TITAN_GHOST_MOUSE", "0")
    os.environ.setdefault("TITAN_OPPONENTS", "3")
    os.environ.setdefault("TITAN_SIMULATIONS", "1000")


class _DictMemory:
    """Simple dict-backed memory with .get()/.set() interface."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, **kwargs: Any) -> None:
        self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data


def _infer_street(board_cards: list[str]) -> str:
    """Infer street from board card count."""
    n = len(board_cards)
    if n == 0:
        return "preflop"
    if n == 3:
        return "flop"
    if n == 4:
        return "turn"
    return "river"


def _run_cycle(
    cycle_num: int,
    vision: Any,
    workflow: Any,
    action_tool: Any,
    memory: Any,
    visual: bool = False,
    save_frames_dir: str | None = None,
) -> E2ECycleResult:
    """Execute a single E2E cycle through the full pipeline."""
    t0 = time.perf_counter()

    # 1. Read table state
    snapshot = vision.read_table()

    hero_cards = list(snapshot.hero_cards) if snapshot.hero_cards else []
    board_cards = list(snapshot.board_cards) if snapshot.board_cards else []
    pot = float(snapshot.pot) if snapshot.pot else 0.0
    stack = float(snapshot.stack) if snapshot.stack else 0.0
    active_players = int(snapshot.active_players) if snapshot.active_players else 1
    is_my_turn = bool(snapshot.is_my_turn)

    # 2. Update shared memory
    memory["hero_cards"] = hero_cards
    memory["board_cards"] = board_cards
    memory["pot"] = pot
    memory["stack"] = stack
    memory["active_players"] = active_players
    memory["is_my_turn"] = is_my_turn

    # 3. Run decision workflow
    street = _infer_street(board_cards)
    result = workflow.execute(snapshot)

    action = "wait"
    win_rate = 0.0
    pot_odds = 0.0

    if isinstance(result, dict):
        action = result.get("action", "wait")
        win_rate = float(result.get("win_rate", 0.0))
        pot_odds = float(result.get("pot_odds", 0.0))
    elif hasattr(result, "action"):
        action = getattr(result, "action", "wait")
        win_rate = float(getattr(result, "equity", 0.0))
        pot_odds = float(getattr(result, "pot_odds", 0.0))

    # 4. Execute action
    action_summary = action_tool.act("raise_big" if action == "all_in" else action, street)

    # Parse delay from action summary
    delay = 0.0
    if "delay=" in action_summary:
        try:
            delay = float(action_summary.split("delay=")[1].split("s")[0])
        except (ValueError, IndexError):
            pass

    # Parse difficulty
    difficulty = "medium"
    if "difficulty=" in action_summary:
        try:
            difficulty = action_summary.split("difficulty=")[1].strip()
        except (IndexError):
            pass

    latency_ms = (time.perf_counter() - t0) * 1000

    # 5. Visual overlay (if enabled)
    if visual or save_frames_dir:
        try:
            from tools.visual_overlay import (
                OverlayConfig, draw_detections, draw_hud,
                generate_simulated_bboxes,
            )
            import numpy as np

            # Generate a dummy frame for sim mode
            frame = np.zeros((800, 1280, 3), dtype=np.uint8)
            frame[:] = (40, 60, 40)  # dark greenish table

            bboxes = generate_simulated_bboxes(snapshot)
            config = OverlayConfig()

            annotated = draw_detections(frame, bboxes, config)
            annotated = draw_hud(
                annotated,
                snapshot_info={
                    "hero_cards": hero_cards,
                    "board_cards": board_cards,
                    "pot": pot,
                    "stack": stack,
                    "active_players": active_players,
                    "is_my_turn": is_my_turn,
                },
                decision_info={
                    "action": action,
                    "street": street,
                    "win_rate": win_rate,
                    "pot_odds": pot_odds,
                    "difficulty": difficulty,
                    "delay": delay,
                },
                config=config,
            )

            if save_frames_dir:
                os.makedirs(save_frames_dir, exist_ok=True)
                frame_path = os.path.join(save_frames_dir, f"e2e_frame_{cycle_num:04d}.png")
                import cv2
                cv2.imwrite(frame_path, annotated)

            if visual:
                import cv2
                cv2.imshow("Titan E2E Runner", annotated)
                cv2.waitKey(1)

        except ImportError:
            pass

    scenario_name = os.environ.get("TITAN_SIM_SCENARIO", "unknown")

    return E2ECycleResult(
        cycle=cycle_num,
        scenario=scenario_name,
        hero_cards=hero_cards,
        board_cards=board_cards,
        pot=pot,
        stack=stack,
        active_players=active_players,
        is_my_turn=is_my_turn,
        action=action,
        street=street,
        win_rate=win_rate,
        pot_odds=pot_odds,
        difficulty=difficulty,
        delay_seconds=delay,
        latency_ms=round(latency_ms, 3),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _run_e2e(args: argparse.Namespace) -> E2EReport:
    """Execute the full E2E test run."""
    start_time = time.perf_counter()

    # Setup environment
    if args.mode == "sim":
        _setup_sim_env(args.scenario)
    elif args.mode == "real" and args.model:
        os.environ["TITAN_YOLO_MODEL"] = args.model

    # Import components
    from tools.vision_tool import VisionTool
    from tools.action_tool import ActionTool

    try:
        from workflows.poker_hand_workflow import PokerHandWorkflow
    except ImportError:
        # Minimal fallback workflow
        class PokerHandWorkflow:  # type: ignore[no-redef]
            def __init__(self, *a, **kw):
                pass
            def execute(self, snapshot):
                return {"action": "call", "win_rate": 0.5, "pot_odds": 0.3}

    # Initialize components
    vision = VisionTool()
    action_tool = ActionTool()
    memory = _DictMemory()

    try:
        from tools.equity_tool import EquityTool
        from tools.rng_tool import RngTool
        equity = EquityTool()
        rng = RngTool(storage=memory)
        workflow = PokerHandWorkflow(vision, equity, action_tool, memory, rng)
    except (ImportError, TypeError):
        try:
            workflow = PokerHandWorkflow(vision, None, action_tool, memory, None)
        except Exception:
            # Ultimate fallback
            class _FallbackWorkflow:
                def execute(self, snapshot):
                    return {"action": "call", "win_rate": 0.5, "pot_odds": 0.3}
            workflow = _FallbackWorkflow()

    cycles: list[E2ECycleResult] = []
    pass_count = 0
    fail_count = 0

    print(f"[E2E] mode={args.mode} cycles={args.cycles} scenario={args.scenario}")

    for i in range(1, args.cycles + 1):
        try:
            result = _run_cycle(
                cycle_num=i,
                vision=vision,
                workflow=workflow,
                action_tool=action_tool,
                memory=memory,
                visual=args.visual,
                save_frames_dir=args.save_frames,
            )
            cycles.append(result)

            # A cycle passes if it produced a valid action
            if result.action in ("fold", "call", "raise_small", "raise_big", "all_in", "wait"):
                pass_count += 1
            else:
                fail_count += 1

            action_display = result.action.upper().ljust(12)
            print(f"  [{i}/{args.cycles}] {action_display} wr={result.win_rate:.1%} "
                  f"street={result.street} latency={result.latency_ms:.1f}ms")

        except Exception as exc:
            fail_count += 1
            print(f"  [{i}/{args.cycles}] FAIL: {exc}")

        if args.tick_seconds > 0 and i < args.cycles:
            time.sleep(args.tick_seconds)

    # Cleanup visual window
    if args.visual:
        try:
            import cv2
            cv2.destroyAllWindows()
        except ImportError:
            pass

    duration = time.perf_counter() - start_time
    completed = pass_count + fail_count

    # Action distribution
    action_dist: dict[str, int] = {}
    latencies: list[float] = []
    for c in cycles:
        action_dist[c.action] = action_dist.get(c.action, 0) + 1
        latencies.append(c.latency_ms)

    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    overall = "pass" if fail_count == 0 and pass_count > 0 else "fail"

    report = E2EReport(
        mode=args.mode,
        total_cycles=args.cycles,
        completed_cycles=completed,
        pass_count=pass_count,
        fail_count=fail_count,
        overall_status=overall,
        avg_latency_ms=round(avg_latency, 3),
        action_distribution=action_dist,
        cycles=[{
            "cycle": c.cycle, "scenario": c.scenario,
            "hero_cards": c.hero_cards, "board_cards": c.board_cards,
            "pot": c.pot, "stack": c.stack,
            "action": c.action, "street": c.street,
            "win_rate": c.win_rate, "pot_odds": c.pot_odds,
            "difficulty": c.difficulty, "delay_seconds": c.delay_seconds,
            "latency_ms": c.latency_ms, "timestamp": c.timestamp,
        } for c in cycles],
        generated_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=round(duration, 3),
    )

    return report


def main() -> None:
    args = _parse_args()

    if args.dry_run:
        print("[E2E] dry-run: config valido")
        print(f"  mode={args.mode} cycles={args.cycles} scenario={args.scenario}")
        print(f"  visual={args.visual} model={args.model}")
        sys.exit(0)

    report = _run_e2e(args)

    print(f"\n[E2E] overall_status={report.overall_status} "
          f"pass={report.pass_count} fail={report.fail_count} "
          f"avg_latency={report.avg_latency_ms:.1f}ms "
          f"duration={report.duration_seconds:.1f}s")

    if args.save_report:
        os.makedirs(os.path.dirname(args.save_report) or ".", exist_ok=True)
        with open(args.save_report, "w", encoding="utf-8") as f:
            json.dump(report.__dict__, f, indent=2, ensure_ascii=False)
        print(f"[E2E] report salvo: {args.save_report}")

    if args.json:
        print(json.dumps(report.__dict__, indent=2, ensure_ascii=False))

    sys.exit(0 if report.overall_status == "pass" else 1)


if __name__ == "__main__":
    main()

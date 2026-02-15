"""Vision profiling CLI — measure YOLO inference latency and throughput.

Captures N frames via :class:`VisionTool`, records per-frame latency,
and produces a JSON summary with FPS, percentiles and optional
historical comparison.

Usage::

    python scripts/vision_profile.py --frames 100 --fps 30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.vision_tool import VisionTool
from utils.config import VisionRuntimeConfig


@dataclass(slots=True)
class VisionProfileSummary:
    frames: int
    target_fps: float
    achieved_fps: float
    duration_seconds: float
    latency_ms_avg: float
    latency_ms_min: float
    latency_ms_max: float
    latency_ms_p50: float
    latency_ms_p95: float
    state_changed_frames: int
    state_changed_ratio: float
    my_turn_frames: int
    my_turn_ratio: float
    frames_with_actions: int
    frames_with_hero_cards: int
    frames_with_board_cards: int
    avg_active_players: float


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    idx = int(round((len(values) - 1) * q))
    idx = max(0, min(len(values) - 1, idx))
    return sorted(values)[idx]


def _ensure_report_dir(path: str) -> Path:
    report_dir = Path(path)
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _build_report(summary: VisionProfileSummary, samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "summary": asdict(summary),
        "sample_count": len(samples),
        "samples": samples,
    }


def run_profile(
    *,
    frames: int,
    target_fps: float,
    warmup_frames: int,
    report_dir: str | None,
    save_samples: bool,
) -> dict[str, Any]:
    frames = max(1, int(frames))
    warmup_frames = max(0, int(warmup_frames))
    target_fps = max(1.0, float(target_fps))

    vision_config = VisionRuntimeConfig()
    vision = VisionTool(
        model_path=vision_config.model_path,
        monitor=vision_config.monitor_region(),
    )

    interval = 1.0 / target_fps

    for _ in range(warmup_frames):
        _ = vision.read_table()

    latencies_ms: list[float] = []
    frame_samples: list[dict[str, Any]] = []

    state_changed_frames = 0
    my_turn_frames = 0
    frames_with_actions = 0
    frames_with_hero_cards = 0
    frames_with_board_cards = 0
    active_players_sum = 0.0

    started_at = time.perf_counter()

    for frame_index in range(frames):
        frame_started_at = time.perf_counter()
        snapshot = vision.read_table()
        read_elapsed = (time.perf_counter() - frame_started_at) * 1000.0
        latencies_ms.append(read_elapsed)

        if snapshot.state_changed:
            state_changed_frames += 1
        if snapshot.is_my_turn:
            my_turn_frames += 1
        if snapshot.action_points:
            frames_with_actions += 1
        if snapshot.hero_cards:
            frames_with_hero_cards += 1
        if snapshot.board_cards:
            frames_with_board_cards += 1
        active_players_sum += max(0, int(snapshot.active_players))

        if save_samples:
            frame_samples.append(
                {
                    "frame": frame_index + 1,
                    "latency_ms": round(read_elapsed, 3),
                    "state_changed": bool(snapshot.state_changed),
                    "is_my_turn": bool(snapshot.is_my_turn),
                    "active_players": int(max(0, snapshot.active_players)),
                    "hero_cards": len(snapshot.hero_cards),
                    "board_cards": len(snapshot.board_cards),
                    "actions_detected": sorted(list(snapshot.action_points.keys())),
                }
            )

        loop_elapsed = time.perf_counter() - frame_started_at
        sleep_for = interval - loop_elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)

    duration_seconds = time.perf_counter() - started_at
    achieved_fps = frames / max(duration_seconds, 1e-9)

    summary = VisionProfileSummary(
        frames=frames,
        target_fps=target_fps,
        achieved_fps=achieved_fps,
        duration_seconds=duration_seconds,
        latency_ms_avg=(sum(latencies_ms) / len(latencies_ms)) if latencies_ms else 0.0,
        latency_ms_min=min(latencies_ms) if latencies_ms else 0.0,
        latency_ms_max=max(latencies_ms) if latencies_ms else 0.0,
        latency_ms_p50=_quantile(latencies_ms, 0.50),
        latency_ms_p95=_quantile(latencies_ms, 0.95),
        state_changed_frames=state_changed_frames,
        state_changed_ratio=state_changed_frames / frames,
        my_turn_frames=my_turn_frames,
        my_turn_ratio=my_turn_frames / frames,
        frames_with_actions=frames_with_actions,
        frames_with_hero_cards=frames_with_hero_cards,
        frames_with_board_cards=frames_with_board_cards,
        avg_active_players=(active_players_sum / frames),
    )

    report_payload = _build_report(summary, frame_samples)

    if report_dir:
        output_dir = _ensure_report_dir(report_dir)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"vision_profile_{stamp}.json"
        with output_file.open("w", encoding="utf-8") as file_obj:
            json.dump(report_payload, file_obj, ensure_ascii=False, indent=2)
        report_payload["report_file"] = str(output_file)

    return report_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile VisionTool runtime performance.")
    parser.add_argument("--frames", type=int, default=180, help="Number of frames to profile.")
    parser.add_argument("--target-fps", type=float, default=30.0, help="Target loop FPS.")
    parser.add_argument("--warmup-frames", type=int, default=5, help="Warm-up frames before measurement.")
    parser.add_argument("--report-dir", type=str, default="", help="Directory to save JSON report file.")
    parser.add_argument("--no-samples", action="store_true", help="Do not include per-frame samples in output.")
    parser.add_argument("--json", action="store_true", help="Output report payload as JSON.")
    args = parser.parse_args()

    report_dir = args.report_dir.strip() or None
    result = run_profile(
        frames=args.frames,
        target_fps=args.target_fps,
        warmup_frames=args.warmup_frames,
        report_dir=report_dir,
        save_samples=not args.no_samples,
    )

    summary = result.get("summary", {})

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print("[VISION-PROFILE] done")
    print(
        "[VISION-PROFILE] "
        f"frames={summary.get('frames', 0)} "
        f"target_fps={summary.get('target_fps', 0):.2f} "
        f"achieved_fps={summary.get('achieved_fps', 0):.2f} "
        f"duration_s={summary.get('duration_seconds', 0):.2f}"
    )
    print(
        "[VISION-PROFILE] "
        f"latency_ms avg={summary.get('latency_ms_avg', 0):.2f} "
        f"p50={summary.get('latency_ms_p50', 0):.2f} "
        f"p95={summary.get('latency_ms_p95', 0):.2f} "
        f"max={summary.get('latency_ms_max', 0):.2f}"
    )
    print(
        "[VISION-PROFILE] "
        f"state_changed_ratio={summary.get('state_changed_ratio', 0):.3f} "
        f"my_turn_ratio={summary.get('my_turn_ratio', 0):.3f} "
        f"actions_frames={summary.get('frames_with_actions', 0)}"
    )

    if "report_file" in result:
        print(f"[VISION-PROFILE] report_file={result['report_file']}")


if __name__ == "__main__":
    main()

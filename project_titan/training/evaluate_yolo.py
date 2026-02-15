"""
Project Titan — YOLO Model Evaluation

Avalia um modelo YOLOv8 treinado em um dataset de teste,
gerando métricas de precisão, recall, mAP e latência por frame.

Uso:
    python training/evaluate_yolo.py --model runs/detect/titan/weights/best.pt --data training/data.yaml
    python training/evaluate_yolo.py --model runs/detect/titan/weights/best.pt --data training/data.yaml --imgsz 640 --save-report reports/eval_report.json
    python training/evaluate_yolo.py --model best.pt --data training/data.yaml --benchmark --benchmark-frames 100

Variáveis de ambiente:
    TITAN_YOLO_DEVICE: dispositivo (ex: 0, cpu). Auto se não definido.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate trained YOLOv8 model")
    parser.add_argument("--model", type=str, required=True, help="Path to trained .pt model")
    parser.add_argument("--data", type=str, default="training/data.yaml", help="Path to data.yaml")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image size")
    parser.add_argument("--batch", type=int, default=16, help="Batch size for validation")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    parser.add_argument("--iou", type=float, default=0.6, help="IoU threshold for NMS")
    parser.add_argument("--split", type=str, default="test", choices=["val", "test"], help="Dataset split to evaluate")
    parser.add_argument("--benchmark", action="store_true", help="Run latency benchmark")
    parser.add_argument("--benchmark-frames", type=int, default=100, dest="benchmark_frames", help="Frames for benchmark")
    parser.add_argument("--save-report", type=str, default=None, dest="save_report", help="Save report JSON")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without running")
    return parser.parse_args()


def _resolve_path(p: str) -> Path:
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return PROJECT_ROOT / pp


def _run_validation(model, args: argparse.Namespace) -> dict[str, Any]:
    """Run YOLO model.val() and extract metrics."""
    device = os.environ.get("TITAN_YOLO_DEVICE", None)

    val_kwargs: dict = {
        "data": str(_resolve_path(args.data)),
        "imgsz": args.imgsz,
        "batch": args.batch,
        "conf": args.conf,
        "iou": args.iou,
        "split": args.split,
        "verbose": True,
    }
    if device is not None:
        val_kwargs["device"] = device

    start = time.perf_counter()
    results = model.val(**val_kwargs)
    duration = time.perf_counter() - start

    metrics: dict[str, Any] = {"duration_seconds": round(duration, 2)}

    if hasattr(results, "results_dict"):
        rd = results.results_dict
        metrics["mAP50"] = rd.get("metrics/mAP50(B)", 0.0)
        metrics["mAP50_95"] = rd.get("metrics/mAP50-95(B)", 0.0)
        metrics["precision"] = rd.get("metrics/precision(B)", 0.0)
        metrics["recall"] = rd.get("metrics/recall(B)", 0.0)

    if hasattr(results, "speed"):
        metrics["speed"] = dict(results.speed)

    return metrics


def _run_benchmark(model, args: argparse.Namespace) -> dict[str, Any]:
    """Benchmark single-image inference latency."""
    import numpy as np

    dummy = np.random.randint(0, 255, (args.imgsz, args.imgsz, 3), dtype=np.uint8)

    # Warmup
    for _ in range(5):
        model.predict(dummy, verbose=False, conf=args.conf)

    latencies: list[float] = []
    for _ in range(args.benchmark_frames):
        t0 = time.perf_counter()
        model.predict(dummy, verbose=False, conf=args.conf)
        latencies.append((time.perf_counter() - t0) * 1000.0)

    arr = np.array(latencies)
    return {
        "frames": args.benchmark_frames,
        "latency_ms_avg": round(float(arr.mean()), 3),
        "latency_ms_p50": round(float(np.percentile(arr, 50)), 3),
        "latency_ms_p95": round(float(np.percentile(arr, 95)), 3),
        "latency_ms_max": round(float(arr.max()), 3),
        "achieved_fps": round(1000.0 / float(arr.mean()), 2) if arr.mean() > 0 else 0.0,
    }


def main() -> None:
    args = _parse_args()
    model_path = _resolve_path(args.model)
    data_path = _resolve_path(args.data)

    print(f"[EVAL] model  = {model_path}")
    print(f"[EVAL] data   = {data_path}")
    print(f"[EVAL] split  = {args.split}")
    print(f"[EVAL] imgsz  = {args.imgsz}")

    if args.dry_run:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": str(model_path),
            "data": str(data_path),
            "dry_run": True,
        }
        print("[EVAL] Dry-run: configuração validada.")
        if args.json:
            print(json.dumps(report, indent=2))
        if args.save_report:
            rp = Path(args.save_report)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return

    if not model_path.exists():
        print(f"[EVAL] ERRO: modelo não encontrado: {model_path}")
        sys.exit(1)
    if not data_path.exists():
        print(f"[EVAL] ERRO: data.yaml não encontrado: {data_path}")
        sys.exit(1)

    try:
        from ultralytics import YOLO
    except ImportError:
        print("[EVAL] ERRO: ultralytics não instalado.")
        sys.exit(1)

    model = YOLO(str(model_path))

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": str(model_path),
        "data": str(data_path),
        "split": args.split,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "iou": args.iou,
    }

    print("[EVAL] Executando validação...")
    report["validation"] = _run_validation(model, args)

    if args.benchmark:
        print(f"[EVAL] Executando benchmark ({args.benchmark_frames} frames)...")
        report["benchmark"] = _run_benchmark(model, args)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        val = report.get("validation", {})
        print(f"\n[EVAL] === Resultados ===")
        print(f"  mAP50:     {val.get('mAP50', 'N/A')}")
        print(f"  mAP50-95:  {val.get('mAP50_95', 'N/A')}")
        print(f"  Precision: {val.get('precision', 'N/A')}")
        print(f"  Recall:    {val.get('recall', 'N/A')}")
        print(f"  Duration:  {val.get('duration_seconds', 'N/A')}s")

        if "benchmark" in report:
            bm = report["benchmark"]
            print(f"\n  === Benchmark ===")
            print(f"  Frames:      {bm['frames']}")
            print(f"  Avg latency: {bm['latency_ms_avg']} ms")
            print(f"  P95 latency: {bm['latency_ms_p95']} ms")
            print(f"  FPS:         {bm['achieved_fps']}")

    if args.save_report:
        rp = Path(args.save_report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[EVAL] report salvo: {rp}")


if __name__ == "__main__":
    main()

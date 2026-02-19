"""
Project Titan — YOLO Training Script

Treina ou continua treinamento de um modelo YOLOv8 para detecção de cartas PLO6.

Uso:
    python training/train_yolo.py --data training/data.yaml --epochs 100
    python training/train_yolo.py --data training/data.yaml --epochs 50 --resume runs/detect/titan/weights/last.pt
    python training/train_yolo.py --data training/data.yaml --epochs 200 --model yolov8s.pt --batch 32 --imgsz 640

Variáveis de ambiente:
    TITAN_YOLO_DEVICE: dispositivo CUDA (ex: 0, cpu). Padrão: auto
    TITAN_YOLO_WORKERS: data loader workers. Padrão: 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train YOLOv8 for Project Titan card detection")
    parser.add_argument("--data", type=str, default="training/data.yaml", help="Path to data.yaml")
    parser.add_argument("--model", type=str, default="yolov8n.pt", help="Base model (yolov8n/s/m/l/x.pt)")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image size")
    parser.add_argument("--resume", type=str, default=None, help="Resume from checkpoint (.pt)")
    parser.add_argument("--project", type=str, default="runs", help="Project output directory")
    parser.add_argument("--name", type=str, default="titan", help="Run name")
    parser.add_argument("--patience", type=int, default=20, help="Early stopping patience")
    parser.add_argument("--lr0", type=float, default=0.01, help="Initial learning rate")
    parser.add_argument("--lrf", type=float, default=0.01, help="Final learning rate factor")
    parser.add_argument("--mosaic", type=float, default=1.0, help="Mosaic augmentation probability")
    parser.add_argument("--flipud", type=float, default=0.0, help="Vertical flip probability (0 for cards)")
    parser.add_argument("--fliplr", type=float, default=0.0, help="Horizontal flip probability (0 for cards)")
    parser.add_argument("--degrees", type=float, default=5.0, help="Random rotation range")
    parser.add_argument("--hsv-h", type=float, default=0.015, dest="hsv_h", help="HSV hue augmentation")
    parser.add_argument("--hsv-s", type=float, default=0.4, dest="hsv_s", help="HSV saturation augmentation")
    parser.add_argument("--hsv-v", type=float, default=0.3, dest="hsv_v", help="HSV value augmentation")
    parser.add_argument("--save-report", type=str, default=None, help="Save training report JSON to path")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without training")
    return parser.parse_args()


def _resolve_data_path(data_arg: str) -> Path:
    """Resolve data.yaml path relative to PROJECT_ROOT if not absolute.

    Checks CWD first so that running from the repo root with
    ``--data project_titan/training/data.yaml`` works correctly
    without doubling the prefix.
    """
    p = Path(data_arg)
    if p.is_absolute():
        return p
    # Prefer CWD resolution (covers running from repo root)
    cwd_candidate = Path.cwd() / p
    if cwd_candidate.exists():
        return cwd_candidate.resolve()
    # Fallback: relative to PROJECT_ROOT (project_titan/)
    return PROJECT_ROOT / p


def _build_train_kwargs(args: argparse.Namespace) -> dict:
    """Build kwargs dict for YOLO model.train()."""
    device = os.environ.get("TITAN_YOLO_DEVICE", None)
    workers = int(os.environ.get("TITAN_YOLO_WORKERS", "4"))

    kwargs: dict = {
        "data": str(_resolve_data_path(args.data)),
        "epochs": args.epochs,
        "batch": args.batch,
        "imgsz": args.imgsz,
        "project": args.project,
        "name": args.name,
        "patience": args.patience,
        "lr0": args.lr0,
        "lrf": args.lrf,
        "mosaic": args.mosaic,
        "flipud": args.flipud,
        "fliplr": args.fliplr,
        "degrees": args.degrees,
        "hsv_h": args.hsv_h,
        "hsv_s": args.hsv_s,
        "hsv_v": args.hsv_v,
        "workers": workers,
        "exist_ok": True,
        "verbose": True,
    }

    if device is not None:
        kwargs["device"] = device

    return kwargs


def _generate_report(args: argparse.Namespace, results, duration_s: float) -> dict:
    """Generate a training report dict."""
    metrics = {}
    if results is not None:
        try:
            metrics = {
                "mAP50": float(getattr(results, "maps", [0.0])[0]) if hasattr(results, "maps") else 0.0,
            }
            if hasattr(results, "results_dict"):
                rd = results.results_dict
                metrics["mAP50"] = rd.get("metrics/mAP50(B)", metrics.get("mAP50", 0.0))
                metrics["mAP50_95"] = rd.get("metrics/mAP50-95(B)", 0.0)
                metrics["precision"] = rd.get("metrics/precision(B)", 0.0)
                metrics["recall"] = rd.get("metrics/recall(B)", 0.0)
        except Exception:
            pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.resume or args.model,
        "data": str(_resolve_data_path(args.data)),
        "epochs": args.epochs,
        "batch": args.batch,
        "imgsz": args.imgsz,
        "duration_seconds": round(duration_s, 2),
        "metrics": metrics,
    }


def main() -> None:
    args = _parse_args()
    data_path = _resolve_data_path(args.data)

    if not data_path.exists():
        print(f"[TRAIN] ERRO: data.yaml não encontrado: {data_path}")
        sys.exit(1)

    print(f"[TRAIN] data       = {data_path}")
    print(f"[TRAIN] model      = {args.resume or args.model}")
    print(f"[TRAIN] epochs     = {args.epochs}")
    print(f"[TRAIN] batch      = {args.batch}")
    print(f"[TRAIN] imgsz      = {args.imgsz}")
    print(f"[TRAIN] patience   = {args.patience}")
    print(f"[TRAIN] project    = {args.project}")
    print(f"[TRAIN] name       = {args.name}")

    if args.dry_run:
        print("[TRAIN] Dry-run: configuração validada com sucesso.")
        report = _generate_report(args, None, 0.0)
        report["dry_run"] = True
        if args.save_report:
            rp = Path(args.save_report)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"[TRAIN] report salvo: {rp}")
        print(json.dumps(report))
        return

    try:
        from ultralytics import YOLO
    except ImportError:
        print("[TRAIN] ERRO: ultralytics não instalado. Execute: pip install ultralytics")
        sys.exit(1)

    if args.resume:
        print(f"[TRAIN] Resuming from: {args.resume}")
        model = YOLO(args.resume)
    else:
        model = YOLO(args.model)

    train_kwargs = _build_train_kwargs(args)
    if args.resume:
        train_kwargs["resume"] = True

    start = time.perf_counter()
    results = model.train(**train_kwargs)
    duration = time.perf_counter() - start

    print(f"[TRAIN] Treinamento concluído em {duration:.1f}s")

    report = _generate_report(args, results, duration)

    if args.save_report:
        rp = Path(args.save_report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[TRAIN] report salvo: {rp}")

    print(f"[TRAIN] train_report={json.dumps(report)}")
    print(f"[TRAIN] Pesos em: {args.project}/{args.name}/weights/best.pt")


if __name__ == "__main__":
    main()

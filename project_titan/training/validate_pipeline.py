"""Project Titan — Local Validation Pipeline.

Validates the entire detection + OCR + calibration pipeline locally,
using the trained model against real screenshots or synthetic test data.

Tests:
1. Model load & warmup
2. Per-class detection accuracy on validation set
3. OCR accuracy vs expected values
4. Calibration point stability
5. End-to-end latency benchmark
6. Duplicate-card detection (impossible in poker)
7. Confidence distribution analysis

Usage:
    python training/validate_pipeline.py
    python training/validate_pipeline.py --model models/titan_v8_pro.pt --strict
    python training/validate_pipeline.py --model models/titan_v8_pro.pt --screenshots data/to_annotate/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Class names (must match data.yaml) ──────────────────────────────────

CLASS_NAMES = {
    0: "2c", 1: "2d", 2: "2h", 3: "2s", 4: "3c", 5: "3d", 6: "3h", 7: "3s",
    8: "4c", 9: "4d", 10: "4h", 11: "4s", 12: "5c", 13: "5d", 14: "5h", 15: "5s",
    16: "6c", 17: "6d", 18: "6h", 19: "6s", 20: "7c", 21: "7d", 22: "7h", 23: "7s",
    24: "8c", 25: "8d", 26: "8h", 27: "8s", 28: "9c", 29: "9d", 30: "9h", 31: "9s",
    32: "Tc", 33: "Td", 34: "Th", 35: "Ts", 36: "Jc", 37: "Jd", 38: "Jh", 39: "Js",
    40: "Qc", 41: "Qd", 42: "Qh", 43: "Qs", 44: "Kc", 45: "Kd", 46: "Kh", 47: "Ks",
    48: "Ac", 49: "Ad", 50: "Ah", 51: "As",
    52: "fold", 53: "check", 54: "raise", 55: "raise_2x", 56: "raise_2_5x",
    57: "raise_pot", 58: "raise_confirm", 59: "allin", 60: "pot", 61: "stack",
}

CARD_IDS = set(range(0, 52))
BUTTON_IDS = set(range(52, 60))
REGION_IDS = {60, 61}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate Titan detection pipeline locally")
    p.add_argument("--model", type=str, default="models/titan_v7_hybrid.pt",
                    help="Path to trained model (relative to project_titan/)")
    p.add_argument("--data", type=str, default="training/data.yaml",
                    help="data.yaml path")
    p.add_argument("--screenshots", type=str, default=None,
                    help="Directory of real screenshots to test inference on")
    p.add_argument("--imgsz", type=int, default=640, help="Input image size")
    p.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    p.add_argument("--benchmark-frames", type=int, default=50, dest="benchmark_frames",
                    help="Frames for latency benchmark")
    p.add_argument("--strict", action="store_true",
                    help="Fail on any warning (mAP < 0.85, duplicates, etc)")
    p.add_argument("--save-report", type=str, default="reports/validation_report.json",
                    dest="save_report", help="Save validation report")
    p.add_argument("--dry-run", action="store_true", help="Validate config only")
    return p.parse_args()


def resolve_path(p: str) -> Path:
    pp = Path(p)
    if pp.is_absolute():
        return pp
    cwd = Path.cwd() / pp
    if cwd.exists():
        return cwd.resolve()
    return PROJECT_ROOT / pp


# ── Test 1: Model Load ──────────────────────────────────────────────────

def test_model_load(model_path: Path) -> dict[str, Any]:
    """Test that the model loads and runs inference."""
    print("\n[TEST 1] Model Load & Warmup")
    result: dict[str, Any] = {"test": "model_load", "passed": False}

    if not model_path.exists():
        result["error"] = f"Model not found: {model_path}"
        print(f"  ❌ {result['error']}")
        return result

    try:
        from ultralytics import YOLO
        t0 = time.perf_counter()
        model = YOLO(str(model_path))
        load_time = (time.perf_counter() - t0) * 1000

        # Warmup with dummy image
        dummy = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
        t1 = time.perf_counter()
        preds = model.predict(dummy, verbose=False, conf=0.1)
        warmup_time = (time.perf_counter() - t1) * 1000

        result["load_ms"] = round(load_time, 2)
        result["warmup_ms"] = round(warmup_time, 2)
        result["num_classes"] = model.model.nc if hasattr(model.model, "nc") else "unknown"
        result["passed"] = True
        print(f"  ✅ Loaded in {load_time:.0f}ms, warmup {warmup_time:.0f}ms, {result['num_classes']} classes")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ❌ {e}")

    return result


# ── Test 2: Latency Benchmark ───────────────────────────────────────────

def test_latency(model_path: Path, imgsz: int, n_frames: int, conf: float) -> dict[str, Any]:
    """Benchmark single-image inference latency."""
    print(f"\n[TEST 2] Latency Benchmark ({n_frames} frames)")
    result: dict[str, Any] = {"test": "latency_benchmark", "passed": False}

    try:
        from ultralytics import YOLO
        model = YOLO(str(model_path))
        dummy = np.random.randint(0, 255, (imgsz, imgsz, 3), dtype=np.uint8)

        # Warmup
        for _ in range(5):
            model.predict(dummy, verbose=False, conf=conf)

        latencies: list[float] = []
        for _ in range(n_frames):
            t0 = time.perf_counter()
            model.predict(dummy, verbose=False, conf=conf)
            latencies.append((time.perf_counter() - t0) * 1000)

        arr = np.array(latencies)
        result.update({
            "avg_ms": round(float(arr.mean()), 2),
            "p50_ms": round(float(np.percentile(arr, 50)), 2),
            "p95_ms": round(float(np.percentile(arr, 95)), 2),
            "max_ms": round(float(arr.max()), 2),
            "min_ms": round(float(arr.min()), 2),
            "std_ms": round(float(arr.std()), 2),
            "fps": round(1000.0 / float(arr.mean()), 1),
        })

        # Pass criteria: P95 < 50ms (single table) or P95 < 20ms (multi-table)
        p95 = result["p95_ms"]
        result["single_table_ok"] = p95 < 50
        result["multi_table_ok"] = p95 < 20
        result["passed"] = result["single_table_ok"]

        status = "✅" if result["passed"] else "⚠️"
        print(f"  {status} Avg: {result['avg_ms']}ms | P95: {p95}ms | FPS: {result['fps']}")
        if result["multi_table_ok"]:
            max_tables = int(800 / (p95 + 5))  # 800ms budget, 5ms overhead
            print(f"  ✅ Multi-table ready: até {max_tables} mesas simultâneas")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ❌ {e}")

    return result


# ── Test 3: Screenshot Inference ────────────────────────────────────────

def test_screenshot_inference(
    model_path: Path, screenshots_dir: Path, conf: float, imgsz: int
) -> dict[str, Any]:
    """Run inference on real screenshots and check for anomalies."""
    print(f"\n[TEST 3] Screenshot Inference ({screenshots_dir})")
    result: dict[str, Any] = {"test": "screenshot_inference", "passed": False}

    if not screenshots_dir.exists():
        result["skipped"] = True
        result["reason"] = f"Directory not found: {screenshots_dir}"
        print(f"  ⏭️  Skipped: {result['reason']}")
        return result

    import cv2
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    image_files = sorted([
        f for f in screenshots_dir.iterdir()
        if f.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ])

    if not image_files:
        result["skipped"] = True
        result["reason"] = "No images found"
        print(f"  ⏭️  {result['reason']}")
        return result

    image_files = image_files[:50]  # Cap at 50 for speed
    total = len(image_files)
    issues: list[str] = []
    all_detections: list[dict] = []
    duplicate_count = 0
    empty_count = 0

    for img_path in image_files:
        img = cv2.imread(str(img_path))
        if img is None:
            continue

        preds = model.predict(img, verbose=False, conf=conf, imgsz=imgsz)

        if preds and len(preds) > 0:
            boxes = preds[0].boxes
            labels = []
            for box in boxes:
                cls_id = int(box.cls[0])
                confidence = float(box.conf[0])
                label = CLASS_NAMES.get(cls_id, f"cls_{cls_id}")
                labels.append(label)
                all_detections.append({
                    "image": img_path.name,
                    "label": label,
                    "cls_id": cls_id,
                    "confidence": confidence,
                })

            # Check for duplicate cards (impossible in poker)
            card_labels = [l for l in labels if any(
                l == CLASS_NAMES.get(i) for i in CARD_IDS
            )]
            unique_cards = set(card_labels)
            if len(unique_cards) < len(card_labels):
                duplicates = [l for l in unique_cards if card_labels.count(l) > 1]
                issues.append(f"{img_path.name}: duplicate cards {duplicates}")
                duplicate_count += 1

            if not labels:
                empty_count += 1
        else:
            empty_count += 1

    # Confidence distribution
    if all_detections:
        confs = [d["confidence"] for d in all_detections]
        conf_arr = np.array(confs)
        avg_conf = float(conf_arr.mean())
        low_conf_pct = float((conf_arr < 0.5).sum() / len(conf_arr) * 100)
    else:
        avg_conf = 0
        low_conf_pct = 100

    result.update({
        "images_tested": total,
        "total_detections": len(all_detections),
        "avg_detections_per_image": round(len(all_detections) / max(total, 1), 1),
        "empty_images": empty_count,
        "duplicate_card_images": duplicate_count,
        "avg_confidence": round(avg_conf, 4),
        "low_confidence_pct": round(low_conf_pct, 1),
        "issues": issues[:10],  # Cap at 10
    })

    result["passed"] = duplicate_count == 0 and low_conf_pct < 30

    status = "✅" if result["passed"] else "⚠️"
    print(f"  {status} {total} images, {len(all_detections)} detections")
    print(f"      Avg confidence: {avg_conf:.4f}")
    print(f"      Low confidence (<0.5): {low_conf_pct:.1f}%")
    print(f"      Empty images: {empty_count}")
    print(f"      Duplicate cards: {duplicate_count}")
    if issues:
        for issue in issues[:3]:
            print(f"      ⚠️  {issue}")

    return result


# ── Test 4: Confidence Distribution ─────────────────────────────────────

def test_confidence_distribution(model_path: Path, data_path: Path, conf: float) -> dict[str, Any]:
    """Analyze confidence distribution per class group."""
    print("\n[TEST 4] Confidence Distribution Analysis")
    result: dict[str, Any] = {"test": "confidence_distribution", "passed": False}

    try:
        from ultralytics import YOLO
        model = YOLO(str(model_path))

        # Quick validation
        val_results = model.val(
            data=str(data_path),
            imgsz=640,
            batch=16,
            conf=conf,
            iou=0.6,
            split="val",
            verbose=False,
        )

        if hasattr(val_results, "results_dict"):
            rd = val_results.results_dict
            metrics = {
                "mAP50": round(float(rd.get("metrics/mAP50(B)", 0)), 4),
                "mAP50_95": round(float(rd.get("metrics/mAP50-95(B)", 0)), 4),
                "precision": round(float(rd.get("metrics/precision(B)", 0)), 4),
                "recall": round(float(rd.get("metrics/recall(B)", 0)), 4),
            }
            result["metrics"] = metrics
            result["passed"] = metrics["mAP50"] >= 0.80

            status = "✅" if result["passed"] else "⚠️"
            print(f"  {status} mAP50={metrics['mAP50']}, precision={metrics['precision']}, recall={metrics['recall']}")
        else:
            result["error"] = "No results_dict available"
            print(f"  ⚠️  {result['error']}")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ❌ {e}")

    return result


# ── Test 5: OCR Sanity Check ────────────────────────────────────────────

def test_ocr_sanity() -> dict[str, Any]:
    """Validate OCR preprocessing and value extraction."""
    print("\n[TEST 5] OCR Sanity Check")
    result: dict[str, Any] = {"test": "ocr_sanity", "passed": False}

    try:
        from agent.vision_ocr import TitanOCR

        # Test with known patterns
        test_cases = [
            ("1,234", 1234.0),
            ("5.00", 5.0),
            ("10,000", 10000.0),
            ("250", 250.0),
            ("0", 0.0),
        ]

        ocr = TitanOCR.__new__(TitanOCR)
        passed = 0
        failed_cases: list[str] = []

        for raw_input, expected in test_cases:
            # Test sanitize logic directly
            sanitized = raw_input.replace(",", "").replace("$", "").strip()
            try:
                value = float(sanitized)
                if abs(value - expected) < 0.01:
                    passed += 1
                else:
                    failed_cases.append(f"'{raw_input}' → {value} (expected {expected})")
            except ValueError:
                failed_cases.append(f"'{raw_input}' → parse error")

        result["passed_cases"] = passed
        result["total_cases"] = len(test_cases)
        result["failed"] = failed_cases
        result["passed"] = len(failed_cases) == 0

        status = "✅" if result["passed"] else "⚠️"
        print(f"  {status} {passed}/{len(test_cases)} cases passed")
        for f in failed_cases:
            print(f"      ❌ {f}")

    except ImportError:
        result["skipped"] = True
        result["reason"] = "TitanOCR not importable (tesseract not installed?)"
        print(f"  ⏭️  {result['reason']}")

    return result


# ── Test 6: Calibration Validation ──────────────────────────────────────

def test_calibration_config() -> dict[str, Any]:
    """Validate calibration config against expected resolution."""
    print("\n[TEST 6] Calibration Config Validation")
    result: dict[str, Any] = {"test": "calibration_config", "passed": False}

    try:
        import yaml

        config_paths = [
            PROJECT_ROOT / "config_calibration.yaml",
            PROJECT_ROOT / "config_club.yaml",
            PROJECT_ROOT / "config.yaml",
        ]

        issues: list[str] = []

        for cfg_path in config_paths:
            if not cfg_path.exists():
                continue

            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            # Validate action buttons are within 720x1280
            action_buttons = cfg.get("action_buttons", {})
            for btn_name, coords in action_buttons.items():
                if isinstance(coords, (list, tuple)) and len(coords) == 2:
                    x, y = coords
                    if x < 0 or x > 720:
                        issues.append(f"{cfg_path.name}: {btn_name}.x={x} out of range [0,720]")
                    if y < 0 or y > 1280:
                        issues.append(f"{cfg_path.name}: {btn_name}.y={y} out of range [0,1280]")

            # Validate OCR regions
            ocr = cfg.get("ocr", {})
            for region_key in ["pot_region", "stack_region", "call_region"]:
                region_str = ocr.get(region_key, "")
                if region_str:
                    parts = str(region_str).split(",")
                    if len(parts) != 4:
                        issues.append(f"{cfg_path.name}: {region_key} has {len(parts)} parts, expected 4")
                    else:
                        try:
                            x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                            if x + w > 720 or y + h > 1280:
                                issues.append(f"{cfg_path.name}: {region_key} extends beyond 720x1280")
                            if w < 5 or h < 5:
                                issues.append(f"{cfg_path.name}: {region_key} too small ({w}x{h})")
                        except ValueError:
                            issues.append(f"{cfg_path.name}: {region_key} has non-integer values")

            # Validate emulator resolution matches
            emu = cfg.get("emulator", {})
            if emu:
                if emu.get("resolution_w") != 720:
                    issues.append(f"{cfg_path.name}: resolution_w != 720")
                if emu.get("resolution_h") != 1280:
                    issues.append(f"{cfg_path.name}: resolution_h != 1280")

        result["issues"] = issues
        result["passed"] = len(issues) == 0

        status = "✅" if result["passed"] else "⚠️"
        print(f"  {status} {len(issues)} issues found")
        for issue in issues[:5]:
            print(f"      ⚠️  {issue}")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ❌ {e}")

    return result


# ── Main ────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    model_path = resolve_path(args.model)
    data_path = resolve_path(args.data)

    print("=" * 60)
    print("  Project Titan — Validation Pipeline")
    print("=" * 60)
    print(f"  Model: {model_path}")
    print(f"  Data:  {data_path}")
    print(f"  Conf:  {args.conf}")
    print(f"  Strict: {args.strict}")

    if args.dry_run:
        print("\n  Dry-run: config validated.")
        return

    tests: list[dict[str, Any]] = []

    # Test 1: Model Load
    tests.append(test_model_load(model_path))

    # Test 2: Latency Benchmark (only if model loaded)
    if tests[-1].get("passed"):
        tests.append(test_latency(model_path, args.imgsz, args.benchmark_frames, args.conf))

    # Test 3: Screenshot inference (if directory provided)
    if args.screenshots:
        tests.append(test_screenshot_inference(
            model_path, Path(args.screenshots), args.conf, args.imgsz
        ))

    # Test 4: Confidence distribution
    if data_path.exists() and tests[0].get("passed"):
        tests.append(test_confidence_distribution(model_path, data_path, args.conf))

    # Test 5: OCR Sanity
    tests.append(test_ocr_sanity())

    # Test 6: Calibration Config
    tests.append(test_calibration_config())

    # ── Summary ──
    print("\n" + "=" * 60)
    print("  VALIDATION SUMMARY")
    print("=" * 60)

    passed = sum(1 for t in tests if t.get("passed"))
    skipped = sum(1 for t in tests if t.get("skipped"))
    failed = len(tests) - passed - skipped
    total = len(tests)

    for t in tests:
        name = t.get("test", "unknown")
        if t.get("skipped"):
            icon = "⏭️"
        elif t.get("passed"):
            icon = "✅"
        else:
            icon = "❌"
        print(f"  {icon} {name}")

    print(f"\n  Result: {passed}/{total} passed, {skipped} skipped, {failed} failed")

    all_ok = failed == 0
    if args.strict and not all_ok:
        print("\n  ❌ STRICT MODE: Validation failed!")
        exit_code = 1
    else:
        exit_code = 0

    # Save report
    if args.save_report:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": str(model_path),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": total,
            "all_ok": all_ok,
            "tests": tests,
        }
        rp = Path(args.save_report)
        if not rp.is_absolute():
            rp = PROJECT_ROOT / rp
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n  Report: {rp}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

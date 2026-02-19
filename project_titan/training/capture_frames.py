"""
Project Titan — PPPoker Screen Capture Tool
============================================

Captures frames from the running PPPoker emulator window at intervals,
saving them for manual annotation (e.g. with Label Studio or CVAT).

This is critical for building a real-data training set with hero cards
(gold borders), opponent showdown cards, and actual button/UI positions.

Usage:
    python training/capture_frames.py                        # Default: 1 FPS, 200 frames
    python training/capture_frames.py --fps 0.5 --max 500    # 1 frame every 2s, 500 total
    python training/capture_frames.py --output data/captured  # Custom output directory
    python training/capture_frames.py --showdown-only         # Only capture during showdown

Keys during capture:
    SPACE  = Save current frame immediately (manual trigger)
    S      = Toggle showdown-only mode
    Q/ESC  = Stop capturing

After capture, annotate using:
    - CVAT: https://cvat.ai (recommended, YOLO export built-in)
    - Label Studio: https://labelstud.io
    - Roboflow: https://roboflow.com

Tips for annotation:
    - Label hero cards WITH gold border in the box (not just the card face)
    - Label opponent showdown cards even if partially visible
    - Label all visible buttons: fold, check, raise
    - For overlapping cards (PLO6 fan), bbox around visible portion is fine
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Capture PPPoker frames for annotation")
    p.add_argument("--output", type=str, default="data/to_annotate",
                    help="Output directory for captured frames")
    p.add_argument("--fps", type=float, default=1.0,
                    help="Capture rate in frames per second (default: 1)")
    p.add_argument("--max", type=int, default=200,
                    help="Maximum number of frames to capture")
    p.add_argument("--title", type=str, default="LDPlayer",
                    help="Emulator window title")
    p.add_argument("--imgsz", type=int, default=0,
                    help="Resize to this size (0 = keep original)")
    p.add_argument("--showdown-only", action="store_true",
                    help="Only save frames when showdown is detected (bright cards at top)")
    p.add_argument("--auto-annotate", action="store_true",
                    help="Run YOLO on each frame to generate pre-annotations")
    p.add_argument("--model", type=str, default="",
                    help="YOLO model for auto-annotation")
    return p.parse_args()


def capture_frame_mss(monitor=None):
    """Capture screen via mss."""
    try:
        import mss
        import numpy as np
    except ImportError:
        print("[ERRO] 'mss' not installed. Run: pip install mss")
        return None

    with mss.mss() as sct:
        target = monitor or sct.monitors[1]
        frame = np.array(sct.grab(target))
    return frame[:, :, :3]  # Remove alpha


def find_emulator_window(title: str):
    """Try to find emulator window position."""
    try:
        from agent.vision_yolo import EmulatorWindow
        win = EmulatorWindow.find(title)
        if win:
            return {
                "left": win.left,
                "top": win.top,
                "width": win.width,
                "height": win.height,
            }
    except Exception:
        pass
    return None


def auto_annotate_frame(frame, model) -> list[str]:
    """Run YOLO on frame and return annotation lines."""
    try:
        results = model.predict(source=frame, verbose=False)
        if not results:
            return []
        result = results[0]
        names = getattr(result, "names", {})
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []

        h, w = frame.shape[:2]
        lines = []
        cls_vals = boxes.cls.tolist() if boxes.cls is not None else []
        xyxy_vals = boxes.xyxy.tolist() if boxes.xyxy is not None else []
        conf_vals = boxes.conf.tolist() if boxes.conf is not None else []

        for idx, (cls_idx, xyxy) in enumerate(zip(cls_vals, xyxy_vals)):
            conf = conf_vals[idx] if idx < len(conf_vals) else 0.0
            if conf < 0.25:
                continue
            x1, y1, x2, y2 = xyxy
            xc = (x1 + x2) / 2.0 / w
            yc = (y1 + y2) / 2.0 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"{int(cls_idx)} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
        return lines
    except Exception:
        return []


def main() -> None:
    args = parse_args()

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Also create labels dir for auto-annotation
    labels_dir = output_dir / "labels"
    labels_dir.mkdir(exist_ok=True)

    print(f"[INFO] PPPoker Frame Capture Tool")
    print(f"  Output:     {output_dir}")
    print(f"  FPS:        {args.fps}")
    print(f"  Max frames: {args.max}")
    print(f"  Emulator:   {args.title}")
    print()

    # Try to find emulator window
    monitor = find_emulator_window(args.title)
    if monitor:
        print(f"  Window found: {monitor}")
    else:
        print("  [WARN] Emulator window not found — using full screen")

    # Load model for auto-annotation
    model = None
    if args.auto_annotate:
        model_path = args.model or os.getenv("TITAN_YOLO_MODEL", "")
        if model_path:
            try:
                from ultralytics import YOLO
                model = YOLO(model_path)
                print(f"  Auto-annotate: {model_path} ({len(model.names)} classes)")
            except Exception as e:
                print(f"  [WARN] Auto-annotate failed: {e}")

    interval = 1.0 / max(args.fps, 0.01)
    count = 0
    session = datetime.now().strftime("%Y%m%d_%H%M%S")

    print()
    print(f"[INFO] Starting capture... Press Ctrl+C to stop")
    print(f"  Saving to: {output_dir}")
    print()

    try:
        import cv2
        import numpy as np
    except ImportError:
        print("[ERRO] opencv-python required. Run: pip install opencv-python")
        return

    try:
        while count < args.max:
            t0 = time.perf_counter()

            frame = capture_frame_mss(monitor)
            if frame is None:
                time.sleep(1)
                continue

            if args.imgsz > 0:
                frame = cv2.resize(frame, (args.imgsz, args.imgsz))

            # Optionally only save during showdown (high saturation at top)
            if args.showdown_only:
                top_region = frame[:frame.shape[0] // 4, :]
                hsv = cv2.cvtColor(top_region, cv2.COLOR_BGR2HSV)
                mean_sat = float(np.mean(hsv[:, :, 1]))
                if mean_sat < 30:  # No cards visible at top
                    elapsed = time.perf_counter() - t0
                    sleep_time = max(0, interval - elapsed)
                    time.sleep(sleep_time)
                    continue

            # Save frame
            fname = f"cap_{session}_{count:04d}.jpg"
            fpath = output_dir / fname
            cv2.imwrite(str(fpath), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])

            # Auto-annotate if model is available
            if model is not None:
                lines = auto_annotate_frame(frame, model)
                lbl_name = f"cap_{session}_{count:04d}.txt"
                with open(labels_dir / lbl_name, "w") as f:
                    f.write("\n".join(lines))
                ann_str = f"  {len(lines)} pre-annotations"
            else:
                ann_str = ""

            count += 1
            print(f"  [{count:4d}/{args.max}] Saved {fname} ({frame.shape[1]}x{frame.shape[0]}){ann_str}")

            elapsed = time.perf_counter() - t0
            sleep_time = max(0, interval - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\n[INFO] Capture stopped by user")

    print(f"\n[OK] Captured {count} frames → {output_dir}")
    if count > 0:
        print(f"\nNext steps:")
        print(f"  1. Annotate in CVAT/Label Studio with these classes:")
        print(f"     Cards: 2c..As (0-51), Buttons: fold(52), check(53), raise(54)")
        print(f"     pot(60), stack(61)")
        print(f"  2. Export as YOLO format")
        print(f"  3. Add to datasets/titan_cards/")
        print(f"  4. Retrain: python training/train_yolo.py --data training/data.yaml")


if __name__ == "__main__":
    main()

"""Diagnostic script — test the PPPoker card reader on a live screenshot.

Usage::

    cd project_titan
    python scripts/test_card_reader.py [--screenshot path/to/screenshot.png]

If no screenshot is given, captures the current screen.
Prints detected cards and saves debug images to ``reports/debug_cards/``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Ensure project_titan is on the path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test PPPoker card reader")
    parser.add_argument(
        "--screenshot", "-s",
        help="Path to a PNG screenshot (default: capture live screen)",
    )
    parser.add_argument(
        "--model", "-m",
        help="YOLO model path (default: from TITAN_YOLO_MODEL env or config)",
    )
    parser.add_argument(
        "--debug", action="store_true", default=True,
        help="Save debug images (default: True)",
    )
    args = parser.parse_args()

    # Enable debug mode
    os.environ["TITAN_CARD_READER_DEBUG"] = "1"

    import cv2
    import numpy as np

    from tools.card_reader import PPPokerCardReader

    reader = PPPokerCardReader()
    print(f"[CardReader] enabled={reader.enabled}")

    # Load or capture frame
    if args.screenshot:
        frame = cv2.imread(args.screenshot)
        if frame is None:
            print(f"ERROR: cannot read {args.screenshot}")
            sys.exit(1)
        print(f"[Frame] loaded from {args.screenshot}  shape={frame.shape}")
    else:
        try:
            import mss
        except ImportError:
            print("ERROR: mss not installed. pip install mss")
            sys.exit(1)
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[1])
            frame = np.array(raw)[:, :, :3]
        print(f"[Frame] captured live  shape={frame.shape}")

    # Run YOLO to get button positions
    model_path = args.model or os.getenv("TITAN_YOLO_MODEL", "")
    if not model_path:
        # Try config files
        try:
            import yaml
            for cfg_name in ("config_club.yaml", "config.yaml"):
                cfg_path = os.path.join(PROJECT_DIR, cfg_name)
                if os.path.isfile(cfg_path):
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = yaml.safe_load(f) or {}
                    mp = (cfg.get("vision", {}) or {}).get("model_path", "")
                    if mp:
                        model_path = os.path.join(PROJECT_DIR, mp)
                        break
        except Exception:
            pass

    action_points: dict[str, tuple[int, int]] = {}
    action_confidence: dict[str, float] = {}   # keep highest-conf per key
    pot_xy: tuple[int, int] | None = None

    if model_path and os.path.isfile(model_path):
        print(f"[YOLO] loading model: {model_path}")
        try:
            from ultralytics import YOLO
            model = YOLO(model_path)
            results = model.predict(source=frame, conf=0.30, verbose=False)
            if results and len(results) > 0:
                result = results[0]
                names = getattr(result, "names", {})
                boxes = getattr(result, "boxes", None)
                if boxes is not None:
                    cls_list = boxes.cls.tolist() if boxes.cls is not None else []
                    xyxy_list = boxes.xyxy.tolist() if boxes.xyxy is not None else []
                    conf_list = boxes.conf.tolist() if boxes.conf is not None else []

                    for idx, (cls_idx, xyxy) in enumerate(zip(cls_list, xyxy_list)):
                        label = names.get(int(cls_idx), "")
                        conf = float(conf_list[idx]) if idx < len(conf_list) else 0.0
                        cx = int((float(xyxy[0]) + float(xyxy[2])) / 2.0)
                        cy = int((float(xyxy[1]) + float(xyxy[3])) / 2.0)
                        print(f"  [{label}] conf={conf:.2f}  center=({cx},{cy})")

                        # Map YOLO labels to action names
                        # Keep only the highest-confidence detection per key
                        lbl = label.strip().lower()
                        key: str | None = None
                        if lbl in {"fold", "f_c", "fc"}:
                            key = "fold"
                        elif lbl in {"check", "checar"}:
                            key = "check"
                        elif lbl in {"raise", "pagar", "bet"}:
                            key = "raise"
                        elif lbl in {"call"}:
                            key = "call"
                        elif lbl in {"pot", "pote"}:
                            key = "pot_indicator"
                        elif lbl in {"stack", "hero_stack"}:
                            key = "stack_indicator"

                        if key is not None:
                            prev_conf = action_confidence.get(key, -1.0)
                            if conf > prev_conf:
                                action_points[key] = (cx, cy)
                                action_confidence[key] = conf
                                if key == "pot_indicator":
                                    pot_xy = (cx, cy)
        except Exception as e:
            print(f"[YOLO] error: {e}")
    else:
        print(f"[YOLO] model not found: {model_path}")
        print("[YOLO] Using manual button positions for right table (1920x1080 dual)")
        # Default positions from screenshot analysis
        action_points = {
            "fold": (1106, 1023),
            "check": (1309, 1020),
            "raise": (1532, 1023),
            "pot_indicator": (1236, 424),
            "stack_indicator": (1365, 669),
        }
        pot_xy = (1236, 424)

    print(f"\n[Action Points] {action_points}")
    print(f"[Pot XY] {pot_xy}")

    if not action_points:
        print("\nERROR: No YOLO detections found. Cannot determine card regions.")
        print("Tip: Pass button positions manually or use a valid YOLO model.")
        sys.exit(1)

    # Run card reader
    t0 = time.perf_counter()
    hero_cards, board_cards = reader.read_cards(frame, action_points, pot_xy)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"\n{'='*60}")
    print(f"  CARD READER RESULTS  ({elapsed_ms:.1f} ms)")
    print(f"{'='*60}")
    print(f"  Hero cards:  {hero_cards}")
    print(f"  Board cards: {board_cards}")
    print(f"{'='*60}")

    if hero_cards or board_cards:
        print("\n✅ Card detection successful!")
    else:
        print("\n❌ No cards detected.")
        print("   Debug images saved to reports/debug_cards/")
        print("   Check the hero/board region crops to verify positioning.")

    # Extra: show region coordinates for debugging
    button_xs = [v[0] for k, v in action_points.items() if k in ("fold", "call", "check", "raise")]
    button_ys = [v[1] for k, v in action_points.items() if k in ("fold", "call", "check", "raise")]
    if button_xs:
        tcx = sum(button_xs) // len(button_xs)
        bty = sum(button_ys) // len(button_ys)
        print(f"\n[Geometry] table_center_x={tcx}  button_y={bty}")
        print(f"[Hero region] y=[{bty - 210}, {bty - 90}]  x=[{tcx - 180}, {tcx + 180}]")
        if pot_xy:
            print(f"[Board region] y=[{pot_xy[1] + 20}, {pot_xy[1] + 130}]  x=[{pot_xy[0] - 200}, {pot_xy[0] + 200}]")


if __name__ == "__main__":
    main()

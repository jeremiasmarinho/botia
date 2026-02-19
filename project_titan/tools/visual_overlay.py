"""
Project Titan — Visual Overlay

Renderiza bounding boxes, labels e decisões sobre frames capturados.
Útil para validação visual do pipeline YOLO + decisão.

Modos:
  - draw_detections: desenha caixas e labels YOLO no frame
  - draw_decision: adiciona painel lateral com decisão do agente
  - draw_hud: HUD completo (detecções + snapshot + decisão)

Uso standalone:
    python -m tools.visual_overlay --image frame.png --model best.pt
    python -m tools.visual_overlay --image frame.png --model best.pt --save output.png
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Data Structures ────────────────────────────────────────────────

@dataclass(slots=True)
class BBox:
    """Bounding box with label and confidence."""
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    confidence: float
    category: str = "unknown"  # hero, board, dead, button, pot, stack, opponent


@dataclass(slots=True)
class OverlayConfig:
    """Visual overlay configuration."""
    font_scale: float = 0.5
    line_thickness: int = 2
    alpha: float = 0.4
    show_confidence: bool = True
    show_hud: bool = True
    hud_width: int = 320
    hud_bg_color: tuple = (30, 30, 30)
    min_confidence: float = 0.25


# ── Color Palette ──────────────────────────────────────────────────

CATEGORY_COLORS: dict[str, tuple[int, int, int]] = {
    "hero":     (0, 255, 0),     # green
    "board":    (255, 200, 0),   # cyan/gold
    "dead":     (128, 128, 128), # gray
    "button":   (255, 100, 50),  # orange
    "pot":      (0, 200, 255),   # yellow-ish
    "stack":    (0, 200, 255),   # yellow-ish
    "opponent": (200, 50, 200),  # purple
    "unknown":  (200, 200, 200), # light gray
}

ACTION_COLORS: dict[str, tuple[int, int, int]] = {
    "fold":        (0, 0, 200),     # red
    "call":        (0, 200, 0),     # green
    "raise_small": (0, 180, 255),   # orange (semantic action)
    "raise_big":   (0, 100, 255),   # deep orange (semantic action)
    "raise":       (0, 180, 255),   # orange (UI button)
    "raise_2x":    (0, 180, 255),   # orange
    "raise_pot":   (0, 100, 255),   # deep orange
    "raise_confirm": (0, 140, 255), # orange-red
    "wait":        (180, 180, 180), # gray
}


# ── Overlay Drawing Functions ──────────────────────────────────────

def _get_cv2():
    """Lazy import cv2."""
    try:
        import cv2
        return cv2
    except ImportError:
        return None


def _get_numpy():
    """Lazy import numpy."""
    try:
        import numpy as np
        return np
    except ImportError:
        return None


def classify_label_category(label: str) -> str:
    """Classify a YOLO label into a visual category."""
    lower = label.lower()
    if lower.startswith(("hero_", "hole_", "hand_", "player_", "h1_", "h2_")):
        return "hero"
    if lower.startswith(("board_", "flop_", "turn_", "river_", "b1_", "b2_", "b3_", "b4_", "b5_")):
        return "board"
    if lower.startswith(("dead_", "burn_", "muck_", "folded_", "dc_")):
        return "dead"
    if lower.startswith(("btn_", "action_", "button_")):
        return "button"
    # New data.yaml names without btn_ prefix
    if lower in ("fold", "check", "raise", "raise_2x", "raise_2_5x",
                  "raise_pot", "raise_confirm", "allin"):
        return "button"
    if lower.startswith("pot"):
        return "pot"
    if lower.startswith(("stack", "hero_stack")):
        return "stack"
    if lower.startswith(("opponent_", "opp_", "villain_")):
        return "opponent"
    return "unknown"


def draw_detections(
    frame: Any,
    bboxes: list[BBox],
    config: OverlayConfig | None = None,
) -> Any:
    """Draw bounding boxes and labels on a frame copy.

    Returns the annotated frame (numpy array).
    """
    cv2 = _get_cv2()
    np = _get_numpy()
    if cv2 is None or np is None:
        return frame

    if config is None:
        config = OverlayConfig()

    overlay = frame.copy()

    for box in bboxes:
        if box.confidence < config.min_confidence:
            continue

        color = CATEGORY_COLORS.get(box.category, CATEGORY_COLORS["unknown"])

        # Draw filled rectangle with alpha
        sub_overlay = overlay.copy()
        cv2.rectangle(sub_overlay, (box.x1, box.y1), (box.x2, box.y2), color, -1)
        cv2.addWeighted(sub_overlay, config.alpha * 0.3, overlay, 1 - config.alpha * 0.3, 0, overlay)

        # Draw border
        cv2.rectangle(overlay, (box.x1, box.y1), (box.x2, box.y2), color, config.line_thickness)

        # Label text
        label_text = box.label
        if config.show_confidence:
            label_text += f" {box.confidence:.0%}"

        (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, config.font_scale, 1)
        label_y = max(box.y1 - 5, th + 5)
        cv2.rectangle(overlay, (box.x1, label_y - th - 4), (box.x1 + tw + 4, label_y + 2), color, -1)
        cv2.putText(overlay, label_text, (box.x1 + 2, label_y - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, config.font_scale, (0, 0, 0), 1, cv2.LINE_AA)

    return overlay


def draw_hud(
    frame: Any,
    snapshot_info: dict[str, Any] | None = None,
    decision_info: dict[str, Any] | None = None,
    config: OverlayConfig | None = None,
) -> Any:
    """Draw a HUD panel on the right side of the frame.

    snapshot_info keys: hero_cards, board_cards, pot, stack, active_players, is_my_turn
    decision_info keys: action, street, win_rate, pot_odds, difficulty, delay
    """
    cv2 = _get_cv2()
    np = _get_numpy()
    if cv2 is None or np is None:
        return frame

    if config is None:
        config = OverlayConfig()

    h, w = frame.shape[:2]
    hud_w = config.hud_width

    # Create extended canvas
    canvas = np.zeros((h, w + hud_w, 3), dtype=np.uint8)
    canvas[:, :w] = frame
    canvas[:, w:] = config.hud_bg_color

    y = 30
    line_h = 22
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = 0.45
    white = (255, 255, 255)
    green = (0, 255, 0)
    yellow = (0, 220, 255)
    red = (0, 80, 255)
    gray = (160, 160, 160)

    def put(text: str, color=white, bold: bool = False):
        nonlocal y
        thickness = 2 if bold else 1
        cv2.putText(canvas, text, (w + 10, y), font, fs, color, thickness, cv2.LINE_AA)
        y += line_h

    # Header
    cv2.putText(canvas, "PROJECT TITAN", (w + 10, y), font, 0.6, green, 2, cv2.LINE_AA)
    y += line_h + 5
    cv2.line(canvas, (w + 5, y), (w + hud_w - 5, y), gray, 1)
    y += 15

    # Snapshot info
    if snapshot_info:
        put("TABLE STATE", color=yellow, bold=True)
        y += 5

        hero = snapshot_info.get("hero_cards", [])
        put(f"Hero: {' '.join(hero) if hero else '---'}", color=green)

        board = snapshot_info.get("board_cards", [])
        put(f"Board: {' '.join(board) if board else '---'}")

        pot = snapshot_info.get("pot", 0)
        stack = snapshot_info.get("stack", 0)
        put(f"Pot: {pot}  Stack: {stack}")

        ap = snapshot_info.get("active_players", "?")
        my_turn = snapshot_info.get("is_my_turn", False)
        turn_color = green if my_turn else red
        put(f"Players: {ap}  My turn: {my_turn}", color=turn_color)

        y += 10
        cv2.line(canvas, (w + 5, y), (w + hud_w - 5, y), gray, 1)
        y += 15

    # Decision info
    if decision_info:
        put("DECISION", color=yellow, bold=True)
        y += 5

        action = decision_info.get("action", "wait")
        action_color = ACTION_COLORS.get(action, white)
        put(f"Action: {action.upper()}", color=action_color, bold=True)

        street = decision_info.get("street", "?")
        put(f"Street: {street}")

        wr = decision_info.get("win_rate")
        if wr is not None:
            wr_color = green if wr >= 0.5 else (yellow if wr >= 0.3 else red)
            put(f"Win rate: {wr:.1%}", color=wr_color)

        po = decision_info.get("pot_odds")
        if po is not None:
            put(f"Pot odds: {po:.1%}")

        diff = decision_info.get("difficulty", "?")
        delay = decision_info.get("delay", 0)
        put(f"Difficulty: {diff}  Delay: {delay:.1f}s")

        y += 10
        cv2.line(canvas, (w + 5, y), (w + hud_w - 5, y), gray, 1)
        y += 15

    # Timestamp
    ts = time.strftime("%H:%M:%S")
    put(f"Time: {ts}", color=gray)

    return canvas


def extract_bboxes_from_yolo(result: Any) -> list[BBox]:
    """Extract BBox list from an ultralytics YOLO result object."""
    bboxes: list[BBox] = []
    names: dict[int, str] = getattr(result, "names", {})
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return bboxes

    xyxy = boxes.xyxy
    confs = boxes.conf
    cls_ids = boxes.cls

    for i in range(len(cls_ids)):
        x1, y1, x2, y2 = [int(v) for v in xyxy[i].tolist()]
        conf = float(confs[i])
        cls_id = int(cls_ids[i])
        label = names.get(cls_id, f"class_{cls_id}")
        category = classify_label_category(label)
        bboxes.append(BBox(x1=x1, y1=y1, x2=x2, y2=y2,
                           label=label, confidence=conf, category=category))
    return bboxes


def generate_simulated_bboxes(snapshot: Any) -> list[BBox]:
    """Generate fake BBox positions from a TableSnapshot for visual testing.

    Creates evenly distributed boxes in a standard poker table layout.
    """
    bboxes: list[BBox] = []
    cx, cy = 640, 400  # center

    # Hero cards (bottom center)
    hero_cards = getattr(snapshot, "hero_cards", []) or []
    for i, card in enumerate(hero_cards):
        x = cx - 100 + i * 55
        bboxes.append(BBox(x1=x, y1=620, x2=x + 45, y2=680,
                           label=f"hero_{card}", confidence=0.95, category="hero"))

    # Board cards (center)
    board_cards = getattr(snapshot, "board_cards", []) or []
    for i, card in enumerate(board_cards):
        x = cx - 130 + i * 55
        bboxes.append(BBox(x1=x, y1=340, x2=x + 45, y2=400,
                           label=f"board_{card}", confidence=0.92, category="board"))

    # Pot (above board)
    pot = getattr(snapshot, "pot", 0) or 0
    if pot > 0:
        bboxes.append(BBox(x1=cx - 50, y1=280, x2=cx + 50, y2=310,
                           label=f"pot_{pot}", confidence=0.88, category="pot"))

    # Action buttons (bottom)
    action_points = getattr(snapshot, "action_points", {}) or {}
    btn_names = ["fold", "check", "raise", "raise_2x", "raise_pot", "raise_confirm"]
    for i, name in enumerate(btn_names):
        if name in action_points:
            pt = action_points[name]
            x = getattr(pt, "x", 500 + i * 120)
            y = getattr(pt, "y", 740)
        else:
            x = 400 + i * 140
            y = 740
        bboxes.append(BBox(x1=x - 40, y1=y - 15, x2=x + 40, y2=y + 15,
                           label=name, confidence=0.90, category="button"))

    return bboxes


# ── Standalone CLI ────────────────────────────────────────────────

def _run_standalone(args: argparse.Namespace) -> None:
    """Run standalone overlay on a saved image."""
    cv2 = _get_cv2()
    np = _get_numpy()
    if cv2 is None:
        print("[ERROR] opencv-python nao instalado (pip install opencv-python)")
        sys.exit(1)

    frame = cv2.imread(args.image)
    if frame is None:
        print(f"[ERROR] Nao foi possivel ler: {args.image}")
        sys.exit(1)

    bboxes: list[BBox] = []
    if args.model:
        try:
            from ultralytics import YOLO
            model = YOLO(args.model)
            results = model.predict(source=frame, verbose=False)
            if results:
                bboxes = extract_bboxes_from_yolo(results[0])
        except ImportError:
            print("[WARN] ultralytics nao instalado, mostrando frame sem deteccoes")

    config = OverlayConfig(
        show_confidence=not args.no_confidence,
        min_confidence=args.min_conf,
    )

    annotated = draw_detections(frame, bboxes, config)
    annotated = draw_hud(
        annotated,
        snapshot_info={
            "hero_cards": [b.label.split("_", 1)[1] for b in bboxes if b.category == "hero"],
            "board_cards": [b.label.split("_", 1)[1] for b in bboxes if b.category == "board"],
            "pot": sum(1 for b in bboxes if b.category == "pot"),
            "active_players": "?",
            "is_my_turn": True,
        },
        config=config,
    )

    if args.save:
        cv2.imwrite(args.save, annotated)
        print(f"[OK] Salvo: {args.save}")
    else:
        cv2.imshow("Titan Visual Overlay", annotated)
        print("[INFO] Pressione Q para sair")
        while True:
            if cv2.waitKey(100) & 0xFF == ord("q"):
                break
        cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description="Titan Visual Overlay - draw YOLO detections + HUD")
    parser.add_argument("--image", required=True, help="Input image path")
    parser.add_argument("--model", default=None, help="YOLO model path (.pt)")
    parser.add_argument("--save", default=None, help="Save annotated image instead of showing")
    parser.add_argument("--no-confidence", action="store_true", help="Hide confidence scores")
    parser.add_argument("--min-conf", type=float, default=0.25, help="Minimum confidence threshold")
    args = parser.parse_args()
    _run_standalone(args)


if __name__ == "__main__":
    main()

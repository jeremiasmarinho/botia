#!/usr/bin/env python3
"""Diagnostic script — captures one frame and prints full pipeline analysis.

Usage:
    cd project_titan
    python -m tools.diagnose_vision

Prints:
    - YOLO model classes
    - Raw detections with labels and Y positions
    - Card zone thresholds (hero/board)
    - How generic cards were assigned
    - Action points (from YOLO vs config)
    - is_my_turn status
    - Card reader fallback status
    - Final snapshot
"""

from __future__ import annotations

import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    print("=" * 60)
    print("TITAN VISION DIAGNOSTIC")
    print("=" * 60)

    # 1. Load config
    print("\n── 1. Configuration ──")
    try:
        from utils.titan_config import cfg
        hero_area = cfg.get_raw("vision.regions.hero_area", None)
        board_area = cfg.get_raw("vision.regions.board_area", None)
        action_coords = {}
        for name in ("fold", "call", "raise"):
            pt = cfg.get_raw(f"action_coordinates.{name}", None)
            if isinstance(pt, dict):
                action_coords[name] = (pt.get("x"), pt.get("y"))
        print(f"  hero_area:          {hero_area}")
        print(f"  board_area:         {board_area}")
        print(f"  action_coordinates: {action_coords}")
        model_path = cfg.get_str("vision.model_path", "")
        print(f"  model_path (config):{model_path}")
        gm_enabled = cfg.get_bool("ghost_mouse.enabled", False)
        print(f"  ghost_mouse.enabled:{gm_enabled}")
    except Exception as e:
        print(f"  Config load error: {e}")

    # 2. Check YOLO model
    print("\n── 2. YOLO Model ──")
    model_env = os.getenv("TITAN_YOLO_MODEL", "")
    print(f"  TITAN_YOLO_MODEL env: {model_env or '(not set)'}")

    from tools.vision_tool import VisionTool
    vt = VisionTool()
    print(f"  VisionTool.model_path: {vt.model_path}")
    print(f"  Hero Y range: {vt._hero_y_min} - {vt._hero_y_max}")
    print(f"  Board Y range: {vt._board_y_min} - {vt._board_y_max}")

    if vt._model is not None:
        names = getattr(vt._model, "names", {})
        print(f"  Model classes ({len(names)}): {list(names.values())[:10]}...")
        has_button_classes = any(
            n.lower() in ("fold", "call", "raise", "check")
            for n in names.values()
        )
        print(f"  Has button classes: {has_button_classes}")
    else:
        print(f"  Model not loaded. Error: {vt._load_error}")

    # 3. Check GhostMouse
    print("\n── 3. GhostMouse ──")
    gm_env = os.getenv("TITAN_GHOST_MOUSE", "")
    print(f"  TITAN_GHOST_MOUSE env: {gm_env or '(not set)'}")
    try:
        from agent.ghost_mouse import GhostMouse, GhostMouseConfig
        gm = GhostMouse(GhostMouseConfig())
        print(f"  GhostMouse._enabled: {gm._enabled}")
    except Exception as e:
        print(f"  GhostMouse error: {e}")

    # 4. Check ActionTool regions
    print("\n── 4. ActionTool Regions ──")
    try:
        from tools.action_tool import ActionTool
        at = ActionTool()
        for name, pt in sorted(at._regions.items()):
            print(f"  {name:20s} → ({pt.x}, {pt.y})")
    except Exception as e:
        print(f"  ActionTool error: {e}")

    # 5. Capture a frame and run YOLO
    print("\n── 5. Live Capture ──")
    if vt._model is None:
        print("  Skipping — model not loaded")
        return

    frame = vt._capture_frame()
    if frame is None:
        print("  Skipping — capture failed (is emulator running?)")
        return

    print(f"  Frame shape: {frame.shape}")

    try:
        results = vt._model.predict(source=frame, verbose=False)
    except Exception as e:
        print(f"  YOLO predict error: {e}")
        return

    if not results:
        print("  No YOLO results")
        return

    result = results[0]
    names = getattr(result, "names", {})
    boxes = getattr(result, "boxes", None)

    if boxes is None:
        print("  No boxes detected")
        return

    cls_values = boxes.cls.tolist() if boxes.cls is not None else []
    xyxy_values = boxes.xyxy.tolist() if boxes.xyxy is not None else []
    conf_values = boxes.conf.tolist() if boxes.conf is not None else []

    print(f"  Total detections: {len(cls_values)}")
    print()
    print("  Raw detections:")
    print(f"  {'Label':8s} {'Conf':6s} {'CX':6s} {'CY':6s} {'Zone':10s}")
    print("  " + "-" * 40)

    for idx, (cls_idx, xyxy) in enumerate(zip(cls_values, xyxy_values)):
        label = names.get(int(cls_idx), "?")
        conf = conf_values[idx] if idx < len(conf_values) else 0.0
        cx = (xyxy[0] + xyxy[2]) / 2
        cy = (xyxy[1] + xyxy[3]) / 2

        zone = "???"
        if vt._hero_y_min <= cy <= vt._hero_y_max:
            zone = "HERO"
        elif vt._board_y_min <= cy <= vt._board_y_max:
            zone = "BOARD"
        else:
            zone = "OTHER"

        print(f"  {label:8s} {conf:5.2f}  {cx:6.1f} {cy:6.1f} {zone:10s}")

    # 6. Run full snapshot
    print("\n── 6. Final Snapshot ──")
    snapshot = vt.read_table()
    print(f"  hero_cards:     {snapshot.hero_cards}")
    print(f"  board_cards:    {snapshot.board_cards}")
    print(f"  pot:            {snapshot.pot}")
    print(f"  stack:          {snapshot.stack}")
    print(f"  call_amount:    {snapshot.call_amount}")
    print(f"  is_my_turn:     {snapshot.is_my_turn}")
    print(f"  state_changed:  {snapshot.state_changed}")
    print(f"  action_points:  {dict(snapshot.action_points)}")
    print(f"  active_players: {snapshot.active_players}")
    print(f"  dead_cards:     {snapshot.dead_cards}")

    # 7. Summary
    print("\n── 7. Health Check ──")
    issues = []
    if not snapshot.hero_cards:
        issues.append("❌ No hero cards detected")
    else:
        print(f"✅ Hero cards: {snapshot.hero_cards}")

    if not snapshot.is_my_turn:
        issues.append("❌ is_my_turn=False — workflow will return 'wait'")
    else:
        print("✅ is_my_turn=True")

    if not any(k in snapshot.action_points for k in ("fold", "call", "raise")):
        issues.append("❌ No action buttons — clicks won't work")
    else:
        print("✅ Action buttons available")

    gm_active = os.getenv("TITAN_GHOST_MOUSE", "0") in ("1", "true", "yes", "on")
    if not gm_active:
        issues.append("⚠️  TITAN_GHOST_MOUSE not set — mouse won't actually move")
    else:
        print("✅ GhostMouse enabled")

    if issues:
        print()
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ All systems operational!")

    print()


if __name__ == "__main__":
    main()

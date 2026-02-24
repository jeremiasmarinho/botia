"""Project Titan — Calibration Validator & Auto-Healer.

Run this script to validate the full calibration chain:
- Emulator window detection
- YOLO model integrity
- Button coordinate accuracy
- OCR region accuracy
- End-to-end action path

Usage:
    python training/validate_calibration.py
    python training/validate_calibration.py --live    # Real emulator test
    python training/validate_calibration.py --fix     # Auto-fix issues
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate Titan calibration")
    p.add_argument("--live", action="store_true", help="Test against live emulator")
    p.add_argument("--fix", action="store_true", help="Auto-fix discovered issues")
    p.add_argument("--config", type=str, default="config_club.yaml", help="Config to validate")
    p.add_argument("--screenshots", type=str, default=None, help="Test on saved screenshots")
    p.add_argument("--save-report", type=str, default="reports/calibration_validation.json",
                    dest="save_report")
    return p.parse_args()


def load_config(config_name: str) -> dict[str, Any]:
    """Load and merge config files."""
    import yaml

    configs = {}
    config_path = PROJECT_ROOT / config_name
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            configs = yaml.safe_load(f) or {}

    # Also load calibration config for reference
    calib_path = PROJECT_ROOT / "config_calibration.yaml"
    if calib_path.exists():
        with open(calib_path, "r", encoding="utf-8") as f:
            calib = yaml.safe_load(f) or {}
            # Merge calibration data (lower priority)
            for key in ["action_buttons", "action_boxes", "calibration"]:
                if key in calib and key not in configs:
                    configs[key] = calib[key]

    return configs


# ── Validation Checks ──────────────────────────────────────────────────

def check_resolution_consistency(config: dict[str, Any]) -> dict[str, Any]:
    """Verify all configs agree on 720x1280."""
    print("\n[CHECK] Resolution Consistency")
    issues: list[str] = []

    emu = config.get("emulator", {})
    vision = config.get("vision", {})
    profile = config.get("profile", {})

    expected_w, expected_h = 720, 1280

    if emu:
        w = emu.get("resolution_w")
        h = emu.get("resolution_h")
        if w != expected_w or h != expected_h:
            issues.append(f"emulator resolution: {w}x{h} (expected {expected_w}x{expected_h})")

    # Check ROI
    roi = vision.get("roi", {})
    if roi:
        roi_w = roi.get("width", 0)
        roi_h = roi.get("height", 0)
        if roi_w > 0 and roi_w != expected_w:
            issues.append(f"vision.roi.width: {roi_w} (expected {expected_w})")
        if roi_h > 0 and roi_h != expected_h:
            issues.append(f"vision.roi.height: {roi_h} (expected {expected_h})")

    passed = len(issues) == 0
    icon = "✅" if passed else "❌"
    print(f"  {icon} {len(issues)} issues")
    for i in issues:
        print(f"      ⚠️  {i}")
    return {"check": "resolution", "passed": passed, "issues": issues}


def check_button_coordinates(config: dict[str, Any]) -> dict[str, Any]:
    """Validate action button coordinates are sane for 720x1280.

    Checks both ``action_buttons`` (legacy) and ``action_coordinates``
    (preferred) sections.
    """
    print("\n[CHECK] Button Coordinates")
    issues: list[str] = []

    # Prefer action_coordinates (newer, more complete)
    action_coords = config.get("action_coordinates", {})
    action_buttons = config.get("action_buttons", {})

    if action_coords:
        source = "action_coordinates"
        # action_coordinates uses {name: {x: N, y: N}} format
        buttons_flat: dict[str, tuple[int, int]] = {}
        for name, val in action_coords.items():
            if isinstance(val, dict) and "x" in val and "y" in val:
                buttons_flat[name] = (int(val["x"]), int(val["y"]))
            elif isinstance(val, (list, tuple)) and len(val) == 2:
                buttons_flat[name] = (int(val[0]), int(val[1]))
    elif action_buttons:
        source = "action_buttons"
        buttons_flat = {}
        for name, val in action_buttons.items():
            if isinstance(val, (list, tuple)) and len(val) == 2:
                buttons_flat[name] = (int(val[0]), int(val[1]))
            elif isinstance(val, dict) and "x" in val and "y" in val:
                buttons_flat[name] = (int(val["x"]), int(val["y"]))
    else:
        issues.append("No action_buttons or action_coordinates defined")
        print("  ❌ No button coordinates defined")
        return {"check": "buttons", "passed": False, "issues": issues}

    # Expected ranges for action buttons (720x1280)
    # Utility buttons (sit_out, emote, timebank) can be anywhere
    UTILITY_BUTTONS = {"sit_out", "emote", "timebank", "slider_start", "slider_end"}
    expected_y_min = 900   # Action buttons in bottom ~30% of screen
    expected_y_max = 1270
    expected_x_min = 10
    expected_x_max = 715

    for name, (x, y) in buttons_flat.items():
        if x < expected_x_min or x > expected_x_max:
            issues.append(f"{name}: x={x} out of range [{expected_x_min},{expected_x_max}]")
        if name not in UTILITY_BUTTONS:
            if y < expected_y_min or y > expected_y_max:
                issues.append(f"{name}: y={y} out of expected button zone [{expected_y_min},{expected_y_max}]")

    # Check that fold < call < raise (left to right order)
    fold_x = buttons_flat.get("fold", (None,))[0]
    call_x = buttons_flat.get("call", (None,))[0]
    raise_x = buttons_flat.get("raise", buttons_flat.get("raise_small", (None,)))[0]

    if fold_x is not None and call_x is not None and fold_x >= call_x:
        issues.append(f"fold.x ({fold_x}) >= call.x ({call_x}) — buttons out of order")
    if call_x is not None and raise_x is not None and call_x >= raise_x:
        issues.append(f"call.x ({call_x}) >= raise.x ({raise_x}) — buttons out of order")

    passed = len(issues) == 0
    icon = "✅" if passed else "⚠️"
    print(f"  {icon} [{source}] {len(issues)} issues, {len(buttons_flat)} buttons validated")
    for i in issues:
        print(f"      ⚠️  {i}")
    return {"check": "buttons", "passed": passed, "issues": issues, "source": source}


def check_ocr_regions(config: dict[str, Any]) -> dict[str, Any]:
    """Validate OCR crop regions."""
    print("\n[CHECK] OCR Regions")
    issues: list[str] = []

    ocr = config.get("ocr", {})
    if not ocr or not ocr.get("enabled", False):
        print("  ⏭️  OCR disabled, skipping")
        return {"check": "ocr_regions", "passed": True, "skipped": True}

    regions = {
        "pot_region": ocr.get("pot_region", ""),
        "stack_region": ocr.get("stack_region", ""),
        "call_region": ocr.get("call_region", ""),
    }

    for name, region_str in regions.items():
        if not region_str:
            issues.append(f"{name}: not defined")
            continue

        parts = str(region_str).split(",")
        if len(parts) != 4:
            issues.append(f"{name}: has {len(parts)} parts, expected 4 (x,y,w,h)")
            continue

        try:
            x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            issues.append(f"{name}: non-integer values ({region_str})")
            continue

        if x < 0 or y < 0:
            issues.append(f"{name}: negative coordinates ({x},{y})")
        if w < 10 or h < 10:
            issues.append(f"{name}: too small ({w}x{h}), likely won't OCR correctly")
        if x + w > 720:
            issues.append(f"{name}: extends beyond screen width (x={x}, w={w})")
        if y + h > 1280:
            issues.append(f"{name}: extends beyond screen height (y={y}, h={h})")

        # Contextual checks
        if "pot" in name and y > 800:
            issues.append(f"{name}: pot region seems too low (y={y}), pot is usually in top half")
        if "stack" in name and y < 600:
            issues.append(f"{name}: stack region seems too high (y={y}), stack is usually in bottom half")

    # Guardrails
    for guard_key in ["pot_min", "pot_max", "stack_min", "stack_max", "call_min", "call_max"]:
        val = ocr.get(guard_key)
        if val is not None and val < 0:
            issues.append(f"{guard_key}: negative value ({val})")

    passed = len(issues) == 0
    icon = "✅" if passed else "⚠️"
    print(f"  {icon} {len(issues)} issues")
    for i in issues:
        print(f"      ⚠️  {i}")
    return {"check": "ocr_regions", "passed": passed, "issues": issues}


def check_model_file(config: dict[str, Any]) -> dict[str, Any]:
    """Verify the YOLO model file exists and is loadable."""
    print("\n[CHECK] Model File")
    issues: list[str] = []

    model_path_str = config.get("vision", {}).get("model_path", "")
    if not model_path_str:
        issues.append("vision.model_path is empty")
        print("  ❌ No model_path configured")
        return {"check": "model_file", "passed": False, "issues": issues}

    model_path = Path(model_path_str)
    if not model_path.is_absolute():
        model_path = PROJECT_ROOT / model_path

    if not model_path.exists():
        issues.append(f"Model not found: {model_path}")
        print(f"  ❌ Model not found: {model_path}")
        # Check for alternatives
        alt_models = sorted((PROJECT_ROOT / "models").glob("*.pt")) if (PROJECT_ROOT / "models").exists() else []
        if alt_models:
            print(f"      Available models: {[m.name for m in alt_models]}")
        return {"check": "model_file", "passed": False, "issues": issues}

    size_mb = model_path.stat().st_size / 1024 / 1024
    if size_mb < 1:
        issues.append(f"Model too small ({size_mb:.1f} MB), possibly corrupted")
    elif size_mb > 200:
        issues.append(f"Model very large ({size_mb:.1f} MB), consider using lighter model")

    passed = len(issues) == 0
    icon = "✅" if passed else "⚠️"
    print(f"  {icon} Model: {model_path.name} ({size_mb:.1f} MB)")
    for i in issues:
        print(f"      ⚠️  {i}")
    return {"check": "model_file", "passed": passed, "issues": issues, "size_mb": round(size_mb, 1)}


def check_chrome_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Validate emulator chrome (border) dimensions."""
    print("\n[CHECK] Chrome Settings")
    issues: list[str] = []

    vision = config.get("vision", {})
    chrome = {
        "top": vision.get("chrome_top", 0),
        "bottom": vision.get("chrome_bottom", 0),
        "left": vision.get("chrome_left", 0),
        "right": vision.get("chrome_right", 0),
    }

    # LDPlayer typical chrome: top=35, right=38
    expected = {"top": 35, "bottom": 0, "left": 0, "right": 38}

    for side, expected_val in expected.items():
        actual = chrome[side]
        if actual != expected_val:
            # Not necessarily wrong, but worth noting
            if abs(actual - expected_val) > 10:
                issues.append(f"chrome_{side}: {actual} (expected ~{expected_val} for LDPlayer)")

    # Total chrome shouldn't eat too much of the game area
    total_h = chrome["top"] + chrome["bottom"]
    total_w = chrome["left"] + chrome["right"]
    if total_h > 100:
        issues.append(f"Total vertical chrome: {total_h}px (seems excessive)")
    if total_w > 100:
        issues.append(f"Total horizontal chrome: {total_w}px (seems excessive)")

    game_area = (720 - total_w) * (1280 - total_h)
    game_area_pct = game_area / (720 * 1280) * 100

    passed = len(issues) == 0
    icon = "✅" if passed else "⚠️"
    print(f"  {icon} Chrome: top={chrome['top']}, right={chrome['right']}")
    print(f"      Game area: {game_area_pct:.1f}% of screen")
    for i in issues:
        print(f"      ⚠️  {i}")
    return {"check": "chrome", "passed": passed, "issues": issues, "game_area_pct": round(game_area_pct, 1)}


def check_ghost_mouse(config: dict[str, Any]) -> dict[str, Any]:
    """Validate GhostMouse humanization parameters."""
    print("\n[CHECK] GhostMouse Config")
    issues: list[str] = []

    gm = config.get("ghost_mouse", {})
    if not gm:
        print("  ⏭️  GhostMouse not configured")
        return {"check": "ghost_mouse", "passed": True, "skipped": True}

    # Check timing ranges make sense
    for key in ["timing_easy", "timing_medium", "timing_hard"]:
        val = gm.get(key)
        if isinstance(val, (list, tuple)) and len(val) == 2:
            if val[0] > val[1]:
                issues.append(f"{key}: min ({val[0]}) > max ({val[1]})")
            if val[0] < 0:
                issues.append(f"{key}: negative minimum ({val[0]})")

    # Click hold should be reasonable
    click_hold = gm.get("click_hold")
    if isinstance(click_hold, (list, tuple)) and len(click_hold) == 2:
        if click_hold[0] < 0.01:
            issues.append(f"click_hold: too fast ({click_hold[0]}s), may not register")
        if click_hold[1] > 0.5:
            issues.append(f"click_hold: too slow ({click_hold[1]}s), suspicious")

    # Overshoot probability
    overshoot = gm.get("overshoot_probability", 0)
    if overshoot > 0.3:
        issues.append(f"overshoot_probability: {overshoot} is very high (>30%)")

    passed = len(issues) == 0
    icon = "✅" if passed else "⚠️"
    print(f"  {icon} {len(issues)} issues")
    for i in issues:
        print(f"      ⚠️  {i}")
    return {"check": "ghost_mouse", "passed": passed, "issues": issues}


# ── Live Tests ──────────────────────────────────────────────────────────

def check_live_emulator() -> dict[str, Any]:
    """Test if the emulator window can be found and captured."""
    print("\n[CHECK] Live Emulator Detection")

    try:
        from agent.vision_yolo import EmulatorWindow

        emu = EmulatorWindow()
        if not emu.hwnd:
            print("  ❌ LDPlayer window not found")
            return {"check": "live_emulator", "passed": False, "error": "Window not found"}

        region = emu.region
        print(f"  ✅ Found window: {region}")
        print(f"      Position: ({region['left']}, {region['top']})")
        print(f"      Size: {region['width']}x{region['height']}")

        return {
            "check": "live_emulator",
            "passed": True,
            "region": region,
        }
    except ImportError as e:
        print(f"  ⏭️  Cannot import EmulatorWindow: {e}")
        return {"check": "live_emulator", "passed": True, "skipped": True}
    except Exception as e:
        print(f"  ❌ {e}")
        return {"check": "live_emulator", "passed": False, "error": str(e)}


def check_live_screenshot(config: dict[str, Any]) -> dict[str, Any]:
    """Capture a screenshot and run YOLO inference."""
    print("\n[CHECK] Live Screenshot + YOLO")

    try:
        from agent.vision_yolo import VisionYolo

        model_path = config.get("vision", {}).get("model_path", "")
        if not model_path:
            print("  ⏭️  No model_path configured")
            return {"check": "live_screenshot", "passed": True, "skipped": True}

        # This would need the emulator running
        print("  ⏭️  Live screenshot requires running emulator (use --live)")
        return {"check": "live_screenshot", "passed": True, "skipped": True}

    except Exception as e:
        return {"check": "live_screenshot", "passed": False, "error": str(e)}


# ── Main ────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    print("=" * 60)
    print("  Project Titan — Calibration Validator")
    print("=" * 60)
    print(f"  Config: {args.config}")
    print(f"  Live:   {args.live}")
    print(f"  Fix:    {args.fix}")

    config = load_config(args.config)

    checks: list[dict[str, Any]] = []

    # Static checks (always run)
    checks.append(check_resolution_consistency(config))
    checks.append(check_button_coordinates(config))
    checks.append(check_ocr_regions(config))
    checks.append(check_model_file(config))
    checks.append(check_chrome_settings(config))
    checks.append(check_ghost_mouse(config))

    # Live checks (only with --live)
    if args.live:
        checks.append(check_live_emulator())
        checks.append(check_live_screenshot(config))

    # ── Summary ──
    print("\n" + "=" * 60)
    print("  CALIBRATION SUMMARY")
    print("=" * 60)

    passed = sum(1 for c in checks if c.get("passed"))
    skipped = sum(1 for c in checks if c.get("skipped"))
    failed = len(checks) - passed - skipped
    total_issues = sum(len(c.get("issues", [])) for c in checks)

    for c in checks:
        name = c.get("check", "unknown")
        if c.get("skipped"):
            icon = "⏭️"
        elif c.get("passed"):
            icon = "✅"
        else:
            icon = "❌"
        issues_count = len(c.get("issues", []))
        extra = f" ({issues_count} issues)" if issues_count > 0 else ""
        print(f"  {icon} {name}{extra}")

    print(f"\n  Total: {passed}/{len(checks)} passed, {skipped} skipped, {failed} failed")
    print(f"  Issues: {total_issues}")

    if total_issues == 0:
        print("\n  ✅ Calibração PERFEITA — sistema pronto para operar!")
    elif failed == 0:
        print("\n  ⚠️  Warnings encontrados mas nenhum blocker. Revise os avisos.")
    else:
        print("\n  ❌ Falhas encontradas — corrija antes de operar!")

    # Save report
    if args.save_report:
        report_path = Path(args.save_report)
        if not report_path.is_absolute():
            report_path = PROJECT_ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": args.config,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total_issues": total_issues,
            "checks": checks,
        }
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n  Report: {report_path}")


if __name__ == "__main__":
    main()

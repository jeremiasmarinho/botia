#!/usr/bin/env python
"""Live integration test: click buttons on PPPoker inside LDPlayer.

Usage:
    python test_live_click.py [action]

Actions: fold, call, raise, screenshot, swipe_test

Before running:
  1. LDPlayer must be running with PPPoker open at a table
  2. You must be at a hand (action buttons visible)

Environment:
  TITAN_GHOST_MOUSE=1           Enable real mouse control
  TITAN_INPUT_BACKEND=ldplayer  Use LDPlayer backend
  TITAN_ANDROID_W=1080          Android virtual width
  TITAN_ANDROID_H=1920          Android virtual height
"""

from __future__ import annotations

import os
import sys
import time

# Set up environment BEFORE imports
os.environ["TITAN_GHOST_MOUSE"] = "1"
os.environ["TITAN_INPUT_BACKEND"] = "ldplayer"
os.environ["TITAN_ANDROID_W"] = "1080"
os.environ["TITAN_ANDROID_H"] = "1920"
os.environ["TITAN_ADB_PATH"] = r"F:\LDPlayer\LDPlayer9\adb.exe"
os.environ["TITAN_ADB_DEVICE"] = "emulator-5554"

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Avoid circular import by importing mouse_protocol first
from tools.mouse_protocol import ClickPoint, GhostMouseConfig


def _get_ghost_mouse():
    """Lazy import to avoid circular import issues."""
    from agent.ghost_mouse import GhostMouse
    return GhostMouse(GhostMouseConfig())


def _find_render_hwnd():
    from agent.ghost_mouse import _find_ldplayer_render_hwnd
    return _find_ldplayer_render_hwnd()


def test_window_discovery():
    """Test that we can find LDPlayer's RenderWindow."""
    hwnd = _find_render_hwnd()
    if hwnd:
        print(f"[OK] LDPlayer RenderWindow found: hwnd=0x{hwnd:08X}")
    else:
        print("[FAIL] LDPlayer RenderWindow NOT found")
        print("       Make sure LDPlayer is running and visible")
        return False
    return True


def test_screenshot():
    """Take a screenshot via ADB and save it."""
    gm = _get_ghost_mouse()
    print("[...] Taking screenshot via ADB...")
    png = gm.take_screenshot()
    if png and len(png) > 100:
        path = os.path.join(os.path.dirname(__file__), "reports", "test_screenshot.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(png)
        print(f"[OK] Screenshot saved: {path} ({len(png):,} bytes)")
        return True
    else:
        print("[FAIL] Screenshot returned empty or failed")
        return False


def test_click(action: str):
    """Execute a single click on a PPPoker button."""
    # Action coordinates (Android 1080x1920)
    coords = {
        "fold":  ClickPoint(x=189, y=1830),
        "call":  ClickPoint(x=542, y=1830),
        "raise": ClickPoint(x=894, y=1830),
    }

    target = coords.get(action)
    if not target:
        print(f"[FAIL] Unknown action: {action}")
        print(f"       Available: {', '.join(coords.keys())}")
        return False

    gm = _get_ghost_mouse()
    print(f"[...] Backend: {gm._input_backend}")
    print(f"[...] Enabled: {gm._enabled}")
    print(f"[...] RenderWindow: {gm._ld_render_hwnd}")

    if not gm._enabled:
        print("[FAIL] GhostMouse not enabled. Check TITAN_GHOST_MOUSE=1")
        return False

    print(f"\n>>> Clicking '{action}' at ({target.x}, {target.y}) in 3 seconds...")
    print(">>> Make sure PPPoker is at a table with action buttons visible!")
    for i in range(3, 0, -1):
        print(f"    {i}...")
        time.sleep(1)

    delay = gm.move_and_click(
        target,
        difficulty="easy",
        relative=False,  # coords are absolute Android coords
        action_name=action,
    )
    print(f"[OK] Click sent! delay={delay:.2f}s")
    return True


def test_swipe():
    """Test swipe gesture (simulates dragging the raise slider)."""
    gm = _get_ghost_mouse()

    if not gm._enabled:
        print("[FAIL] GhostMouse not enabled. Check TITAN_GHOST_MOUSE=1")
        return False

    start = ClickPoint(x=150, y=1740)
    end = ClickPoint(x=540, y=1740)  # ~50% of slider

    print(f"\n>>> Swiping from ({start.x},{start.y}) to ({end.x},{end.y}) in 3 seconds...")
    print(">>> Make sure the Raise modal is open!")
    for i in range(3, 0, -1):
        print(f"    {i}...")
        time.sleep(1)

    duration = gm.swipe(start, end, duration=0.5, action_name="slider_test")
    print(f"[OK] Swipe completed! duration={duration:.2f}s")
    return True


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "screenshot"
    print("=" * 60)
    print(f"  Project Titan — Live Click Test")
    print(f"  Action: {action}")
    print("=" * 60)

    # Step 1: Window discovery
    if not test_window_discovery():
        sys.exit(1)

    print()

    # Step 2: Execute requested test
    if action == "screenshot":
        ok = test_screenshot()
    elif action == "swipe_test":
        ok = test_swipe()
    elif action in ("fold", "call", "raise"):
        ok = test_click(action)
    else:
        print(f"[?] Unknown action: {action}")
        print(f"    Available: fold, call, raise, screenshot, swipe_test")
        sys.exit(1)

    print()
    if ok:
        print("=== TEST PASSED ===")
    else:
        print("=== TEST FAILED ===")
        sys.exit(1)


if __name__ == "__main__":
    main()

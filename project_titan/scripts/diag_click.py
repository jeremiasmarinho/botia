#!/usr/bin/env python3
"""Diagnostic: why clicks do NOT register in PPPoker/Unity inside LDPlayer.

Tests each backend in isolation with before/after screenshot comparison.
Targets the CHECK button visible in the screenshot (center button ~275, 960
in the 598x1064 window area).

The script also dumps ALL HWND children of LDPlayer to find the correct
render surface, checks wm size, and tries multiple coordinate systems.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADB = os.getenv("TITAN_ADB_PATH", r"F:\LDPlayer\LDPlayer9\adb.exe")
DEV = os.getenv("TITAN_ADB_DEVICE", "emulator-5554")
LDCON = r"F:\LDPlayer\LDPlayer9\ldconsole.exe"

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_MOUSEMOVE   = 0x0200
MK_LBUTTON     = 0x0001

# --- From the screenshot: Check button is at ~275,960 in Android 720x1280
# But let's also try the Fold button which has a strong red color.
# Fold ~ (100, 960), Check ~ (275, 960), Bet ~ (455, 960)
# Looking at the screenshot more carefully - the buttons are at the very bottom.
# In 720x1280 android coords:
#   Fold:  ~(100, 1230)
#   Check: ~(270, 1230) 
#   Bet:   ~(450, 1230)

TARGETS = {
    "check": (270, 1230),
    "fold":  (100, 1230),
    "bet":   (450, 1230),
}

os.makedirs("reports", exist_ok=True)


def screenshot() -> np.ndarray | None:
    """Capture screenshot via ADB."""
    try:
        r = subprocess.run(
            [ADB, "-s", DEV, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0 and len(r.stdout) > 100:
            arr = np.frombuffer(r.stdout, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return img
    except Exception as e:
        print(f"  Screenshot error: {e}")
    return None


def save(img: np.ndarray, name: str):
    cv2.imwrite(f"reports/diag_{name}.png", img)


def diff_images(before: np.ndarray, after: np.ndarray) -> int:
    """Return number of pixels that changed significantly."""
    if before.shape != after.shape:
        return -1
    d = cv2.absdiff(before, after)
    changed = int(np.sum(np.any(d > 15, axis=2)))
    return changed


def wm_size_check():
    """Check if wm size override is active."""
    print("\n=== WM SIZE CHECK ===")
    r = subprocess.run(
        [ADB, "-s", DEV, "shell", "wm", "size"],
        capture_output=True, text=True, timeout=5,
    )
    print(f"  {r.stdout.strip()}")
    if "Override" in r.stdout:
        print("  WARNING: Override active! Resetting...")
        subprocess.run(
            [ADB, "-s", DEV, "shell", "wm", "size", "reset"],
            capture_output=True, timeout=5,
        )
        time.sleep(1)
        r2 = subprocess.run(
            [ADB, "-s", DEV, "shell", "wm", "size"],
            capture_output=True, text=True, timeout=5,
        )
        print(f"  After reset: {r2.stdout.strip()}")


def enumerate_all_windows():
    """Find ALL LDPlayer-related windows and dump their class names + sizes."""
    print("\n=== ALL LDPLAYER WINDOWS ===")
    all_windows: list[dict] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_top(hwnd, _lp):
        if not _u32.IsWindowVisible(hwnd):
            return True
        cname = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(hwnd, cname, 256)
        title = ctypes.create_unicode_buffer(256)
        _u32.GetWindowTextW(hwnd, title, 256)
        cn = cname.value
        tt = title.value
        
        # Check if it's LDPlayer related
        is_ld = any(x in cn.lower() for x in ("ldplayer", "ld", "render", "sub", "therender"))
        is_ld = is_ld or any(x in tt.lower() for x in ("ldplayer", "pppoker", "ld"))
        
        if is_ld:
            crect = wintypes.RECT()
            _u32.GetClientRect(hwnd, ctypes.byref(crect))
            wrect = wintypes.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(wrect))
            info = {
                "hwnd": hwnd,
                "class": cn,
                "title": tt,
                "client_w": crect.right,
                "client_h": crect.bottom,
                "win_rect": (wrect.left, wrect.top, wrect.right, wrect.bottom),
            }
            all_windows.append(info)
            print(f"  TOP: hwnd={hwnd:#010x} class={cn!r:30s} title={tt!r:30s} client={crect.right}x{crect.bottom}")
            
            # Enumerate ALL children
            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def enum_child(child, _lp2):
                cn2 = ctypes.create_unicode_buffer(256)
                _u32.GetClassNameW(child, cn2, 256)
                tt2 = ctypes.create_unicode_buffer(256)
                _u32.GetWindowTextW(child, tt2, 256)
                cr2 = wintypes.RECT()
                _u32.GetClientRect(child, ctypes.byref(cr2))
                child_info = {
                    "hwnd": child,
                    "class": cn2.value,
                    "title": tt2.value,
                    "client_w": cr2.right,
                    "client_h": cr2.bottom,
                    "parent": hwnd,
                }
                all_windows.append(child_info)
                print(f"    CHILD: hwnd={child:#010x} class={cn2.value!r:25s} title={tt2.value!r:20s} client={cr2.right}x{cr2.bottom}")
                return True
            
            _u32.EnumChildWindows(hwnd, enum_child, 0)
        return True

    _u32.EnumWindows(enum_top, 0)
    return all_windows


def find_best_render_hwnd(windows: list[dict]) -> list[dict]:
    """Find the most likely render surface(s) - largest client area children."""
    children = [w for w in windows if "parent" in w and w["client_w"] > 50 and w["client_h"] > 50]
    children.sort(key=lambda w: w["client_w"] * w["client_h"], reverse=True)
    
    if not children:
        # Use top-level windows
        tops = [w for w in windows if "parent" not in w and w["client_w"] > 50]
        tops.sort(key=lambda w: w["client_w"] * w["client_h"], reverse=True)
        return tops[:3]
    
    return children[:3]


def test_sendmessage(hwnd: int, cw: int, ch: int, ax: int, ay: int, label: str) -> int:
    """Test SendMessage click and return pixel diff."""
    before = screenshot()
    if before is None:
        return -1
    save(before, f"{label}_before")
    
    px = int(ax / 720 * cw)
    py = int(ay / 1280 * ch)
    lp = (py << 16) | (px & 0xFFFF)
    
    print(f"  SendMessage to hwnd={hwnd:#x}: android({ax},{ay}) -> pixel({px},{py})")
    
    # Send mouse move first (some apps need this)
    _u32.SendMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.05)
    _u32.SendMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(0.08)
    _u32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    
    d = diff_images(before, after)
    return d


def test_postmessage(hwnd: int, cw: int, ch: int, ax: int, ay: int, label: str) -> int:
    """Test PostMessage click."""
    before = screenshot()
    if before is None:
        return -1
    
    px = int(ax / 720 * cw)
    py = int(ay / 1280 * ch)
    lp = (py << 16) | (px & 0xFFFF)
    
    print(f"  PostMessage to hwnd={hwnd:#x}: android({ax},{ay}) -> pixel({px},{py})")
    
    _u32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.05)
    _u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(0.08)
    _u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    
    return diff_images(before, after)


def test_adb_tap(ax: int, ay: int, label: str) -> int:
    """Test ADB input tap."""
    before = screenshot()
    if before is None:
        return -1
    
    print(f"  ADB: input touchscreen tap {ax} {ay}")
    subprocess.run(
        [ADB, "-s", DEV, "shell", "input", "touchscreen", "tap", str(ax), str(ay)],
        capture_output=True, timeout=5,
    )
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    return diff_images(before, after)


def test_adb_input_tap(ax: int, ay: int, label: str) -> int:
    """Test ADB input tap (without touchscreen qualifier)."""
    before = screenshot()
    if before is None:
        return -1
    
    print(f"  ADB: input tap {ax} {ay}")
    subprocess.run(
        [ADB, "-s", DEV, "shell", "input", "tap", str(ax), str(ay)],
        capture_output=True, timeout=5,
    )
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    return diff_images(before, after)


def test_ldconsole(ax: int, ay: int, label: str) -> int:
    """Test ldconsole action."""
    if not os.path.isfile(LDCON):
        print("  SKIP: ldconsole not found")
        return -1
    
    before = screenshot()
    if before is None:
        return -1
    
    # Try standard action
    print(f"  ldconsole: action --index 0 --key call.input --value \"{ax} {ay}\"")
    r = subprocess.run(
        [LDCON, "action", "--index", "0", "--key", "call.input", "--value", f"{ax} {ay}"],
        capture_output=True, text=True, timeout=5,
    )
    print(f"    exit={r.returncode} out={r.stdout.strip()} err={r.stderr.strip()}")
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    return diff_images(before, after)


def test_ldconsole_operaterecord(ax: int, ay: int, label: str) -> int:
    """Test ldconsole operaterecord (replay a tap gesture)."""
    if not os.path.isfile(LDCON):
        print("  SKIP: ldconsole not found")
        return -1
    
    before = screenshot()
    if before is None:
        return -1
    
    # operaterecord replays a recorded touch event
    import json
    content = json.dumps([{"timing": 0, "eid": 1, "sx": ax, "sy": ay}])
    print(f"  ldconsole: operaterecord --index 0 --content '{content}'")
    r = subprocess.run(
        [LDCON, "operaterecord", "--index", "0", "--content", content],
        capture_output=True, text=True, timeout=5,
    )
    print(f"    exit={r.returncode} out={r.stdout.strip()} err={r.stderr.strip()}")
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    return diff_images(before, after)


def test_sendevent_interpolated(ax: int, ay: int, label: str) -> int:
    """Test raw sendevent with correct axis interpolation."""
    before = screenshot()
    if before is None:
        return -1
    
    # Discovered axes: X=[0,1279] Y=[0,719], rotated 90deg
    touch_x = int(ay / 1280 * 1279)
    touch_y = int(ax / 720 * 719)
    
    d = "/dev/input/event2"
    print(f"  sendevent: display({ax},{ay}) -> touch({touch_x},{touch_y})")
    
    down = (
        f"sendevent {d} 3 47 0;"
        f"sendevent {d} 3 57 1;"
        f"sendevent {d} 3 53 {touch_x};"
        f"sendevent {d} 3 54 {touch_y};"
        f"sendevent {d} 3 58 1;"
        f"sendevent {d} 1 330 1;"
        f"sendevent {d} 1 325 1;"
        f"sendevent {d} 0 0 0"
    )
    up = (
        f"sendevent {d} 3 47 0;"
        f"sendevent {d} 3 57 -1;"
        f"sendevent {d} 1 330 0;"
        f"sendevent {d} 1 325 0;"
        f"sendevent {d} 0 0 0"
    )
    
    subprocess.run([ADB, "-s", DEV, "shell", down], capture_output=True, timeout=5)
    time.sleep(0.06)
    subprocess.run([ADB, "-s", DEV, "shell", up], capture_output=True, timeout=5)
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    return diff_images(before, after)


def test_physical_mouse_click(hwnd: int, cw: int, ch: int, ax: int, ay: int, label: str) -> int:
    """Test by physically moving the mouse to the window and clicking via SetCursorPos + mouse_event."""
    before = screenshot()
    if before is None:
        return -1
    
    # Get screen coordinates of the client area
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    
    pt = POINT(0, 0)
    _u32.ClientToScreen(hwnd, ctypes.byref(pt))
    
    px = pt.x + int(ax / 720 * cw)
    py = pt.y + int(ay / 1280 * ch)
    
    print(f"  Physical mouse: android({ax},{ay}) -> screen({px},{py})")
    print(f"    Client origin: ({pt.x},{pt.y}), size: {cw}x{ch}")
    
    # Move cursor and click via mouse_event
    _u32.SetCursorPos(px, py)
    time.sleep(0.1)
    
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    _u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    _u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(1.0)
    
    after = screenshot()
    if after is None:
        return -1
    save(after, f"{label}_after")
    return diff_images(before, after)


def main():
    target_name = sys.argv[1] if len(sys.argv) > 1 else "check"
    ax, ay = TARGETS.get(target_name, TARGETS["check"])
    
    print("=" * 65)
    print("  CLICK DIAGNOSIS - Project Titan")
    print("=" * 65)
    print(f"Target button: {target_name} at Android ({ax}, {ay})")
    print(f"ADB: {ADB}")
    print(f"Device: {DEV}")
    
    # Step 1: Check wm size
    wm_size_check()
    
    # Step 2: Enumerate ALL windows
    all_wins = enumerate_all_windows()
    
    # Step 3: Find best render candidates
    candidates = find_best_render_hwnd(all_wins)
    print(f"\n=== BEST RENDER CANDIDATES ({len(candidates)}) ===")
    for c in candidates:
        print(f"  hwnd={c['hwnd']:#010x} class={c['class']!r} size={c['client_w']}x{c['client_h']}")
    
    # Step 4: Get actual screenshot to measure Android resolution
    print("\n=== SCREENSHOT RESOLUTION ===")
    img = screenshot()
    if img is not None:
        print(f"  ADB screenshot: {img.shape[1]}x{img.shape[0]}")
        save(img, "baseline")
    else:
        print("  ERROR: Cannot take screenshot!")
        return
    
    android_w = img.shape[1]
    android_h = img.shape[0]
    
    results: dict[str, int] = {}
    
    # Step 5: Test each method
    print("\n" + "=" * 65)
    print("  TESTING BACKENDS")
    print("=" * 65)
    
    # 5a: ADB input touchscreen tap
    print(f"\n--- [A] ADB input touchscreen tap ---")
    d = test_adb_tap(ax, ay, "A_adb_ts")
    results["ADB touchscreen tap"] = d
    print(f"  Pixel diff: {d}")
    time.sleep(1)
    
    # 5b: ADB input tap (ohne touchscreen)
    print(f"\n--- [B] ADB input tap (no qualifier) ---")
    d = test_adb_input_tap(ax, ay, "B_adb_plain")
    results["ADB plain tap"] = d
    print(f"  Pixel diff: {d}")
    time.sleep(1)
    
    # 5c: ldconsole action
    print(f"\n--- [C] LDConsole action ---")
    d = test_ldconsole(ax, ay, "C_ldconsole")
    results["LDConsole action"] = d
    print(f"  Pixel diff: {d}")
    time.sleep(1)
    
    # 5d: ldconsole operaterecord
    print(f"\n--- [D] LDConsole operaterecord ---")
    d = test_ldconsole_operaterecord(ax, ay, "D_ldconsole_rec")
    results["LDConsole operaterecord"] = d
    print(f"  Pixel diff: {d}")
    time.sleep(1)
    
    # 5e: sendevent with correct interpolation
    print(f"\n--- [E] Raw sendevent (interpolated) ---")
    d = test_sendevent_interpolated(ax, ay, "E_sendevent")
    results["sendevent (interp)"] = d
    print(f"  Pixel diff: {d}")
    time.sleep(1)
    
    # 5f-h: Win32 SendMessage on each candidate HWND
    for idx, cand in enumerate(candidates[:3]):
        hwnd = cand["hwnd"]
        cw, ch = cand["client_w"], cand["client_h"]
        cname = cand["class"]
        
        print(f"\n--- [F{idx}] SendMessage hwnd={hwnd:#x} class={cname!r} {cw}x{ch} ---")
        d = test_sendmessage(hwnd, cw, ch, ax, ay, f"F{idx}_sm_{hwnd:#x}")
        results[f"SendMsg {cname} {hwnd:#x}"] = d
        print(f"  Pixel diff: {d}")
        time.sleep(1)
    
    # 5i: Physical mouse click on best candidate
    if candidates:
        best = candidates[0]
        print(f"\n--- [G] Physical mouse click (SetCursorPos + mouse_event) ---")
        print(f"  WARNING: This will move your physical mouse!")
        d = test_physical_mouse_click(
            best["hwnd"], best["client_w"], best["client_h"],
            ax, ay, "G_physical"
        )
        results["Physical mouse"] = d
        print(f"  Pixel diff: {d}")
    
    # Step 6: Summary
    print("\n" + "=" * 65)
    print("  RESULTS SUMMARY")
    print("=" * 65)
    print(f"  Target: {target_name} at Android ({ax},{ay})")
    print(f"  Android resolution: {android_w}x{android_h}")
    print()
    
    for name, px_diff in sorted(results.items(), key=lambda x: x[1], reverse=True):
        if px_diff < 0:
            status = "ERROR"
        elif px_diff > 5000:
            status = ">>> CLICK REGISTERED <<<"
        elif px_diff > 500:
            status = "MAYBE (small change)"
        else:
            status = "NO EFFECT"
        print(f"  {name:40s}: {px_diff:8d} px changed  {status}")
    
    print()
    print("Diagnostics saved to reports/diag_*.png")
    print("Compare diag_*_before.png vs diag_*_after.png to see which worked.")


if __name__ == "__main__":
    main()

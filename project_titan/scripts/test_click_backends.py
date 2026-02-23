#!/usr/bin/env python3
"""Live test: valida todas as 5 estrategias de click injection do GhostMouse v3.

Testa cada backend isoladamente e reporta qual funciona no PPPoker/Unity.

Uso:
    python scripts/test_click_backends.py [call|fold|raise|centre|custom X Y]

Exemplos:
    python scripts/test_click_backends.py call
    python scripts/test_click_backends.py custom 361 1220
    python scripts/test_click_backends.py          # default: centre da tela
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import ctypes
import ctypes.wintypes as wintypes

# ── Setup paths ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADB_EXE = os.getenv("TITAN_ADB_PATH", r"F:\LDPlayer\LDPlayer9\adb.exe")
ADB_DEVICE = os.getenv("TITAN_ADB_DEVICE", "emulator-5554")
LDCONSOLE = r"F:\LDPlayer\LDPlayer9\ldconsole.exe"

# ── Coordinates (Android native 720x1280) ──
TARGETS = {
    "centre": (360, 640),
    "fold":   (126, 1220),
    "call":   (361, 1220),
    "raise":  (596, 1220),
}

_user32 = ctypes.windll.user32 if os.name == "nt" else None


def get_target(args: list[str]) -> tuple[int, int]:
    if not args:
        return TARGETS["centre"]
    if args[0] == "custom" and len(args) >= 3:
        return (int(args[1]), int(args[2]))
    return TARGETS.get(args[0], TARGETS["centre"])


def find_render_hwnd() -> int | None:
    """Find LDPlayer's RenderWindow HWND."""
    if _user32 is None:
        return None
    result = []
    render_classes = {"RenderWindow", "TheRender", "sub"}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_top(hwnd, _lp):
        if not _user32.IsWindowVisible(hwnd):
            return True
        cname = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cname, 256)
        if cname.value in ("LDPlayerMainFrame", "LDPlayer"):
            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def enum_child(child, _lp2):
                cn2 = ctypes.create_unicode_buffer(256)
                _user32.GetClassNameW(child, cn2, 256)
                if cn2.value in render_classes:
                    result.append(child)
                    return False
                return True
            _user32.EnumChildWindows(hwnd, enum_child, 0)
            if result:
                return False
        return True

    _user32.EnumWindows(enum_top, 0)
    if not result:
        for cls in ("LDPlayerMainFrame", "LDPlayer"):
            h = _user32.FindWindowW(cls, None)
            if h:
                result.append(h)
                break
    return result[0] if result else None


def screenshot(label: str) -> bytes | None:
    """Take ADB screenshot and save with label."""
    try:
        r = subprocess.run(
            [ADB_EXE, "-s", ADB_DEVICE, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0 and len(r.stdout) > 100:
            path = f"reports/click_test_{label}.png"
            os.makedirs("reports", exist_ok=True)
            with open(path, "wb") as f:
                f.write(r.stdout)
            print(f"    Screenshot saved: {path}")
            return r.stdout
    except Exception as e:
        print(f"    Screenshot failed: {e}")
    return None


def test_win32_sendmessage(hwnd: int, x: int, y: int) -> bool:
    """Test 1: Win32 SendMessage WM_LBUTTONDOWN/UP."""
    print(f"\n[1] Win32 SendMessage (hwnd={hwnd:#x})")
    if _user32 is None:
        print("    SKIP: Not on Windows")
        return False

    crect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(crect))
    cw, ch = crect.right, crect.bottom
    print(f"    Client area: {cw}x{ch}")

    px = int(x / 720 * cw)
    py = int(y / 1280 * ch)
    print(f"    Android ({x},{y}) -> Client pixel ({px},{py})")

    lparam = (py << 16) | (px & 0xFFFF)

    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001

    _user32.SendMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    _user32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    print("    SendMessage sent OK")
    time.sleep(0.5)
    screenshot("1_sendmessage")
    return True


def test_win32_postmessage(hwnd: int, x: int, y: int) -> bool:
    """Test 2: Win32 PostMessage (async variant)."""
    print(f"\n[2] Win32 PostMessage (hwnd={hwnd:#x})")
    if _user32 is None:
        print("    SKIP: Not on Windows")
        return False

    crect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(crect))
    cw, ch = crect.right, crect.bottom
    px = int(x / 720 * cw)
    py = int(y / 1280 * ch)
    lparam = (py << 16) | (px & 0xFFFF)

    _user32.PostMessageW(hwnd, 0x0201, 0x0001, lparam)
    time.sleep(0.05)
    _user32.PostMessageW(hwnd, 0x0202, 0, lparam)
    print("    PostMessage sent OK")
    time.sleep(0.5)
    screenshot("2_postmessage")
    return True


def test_ldconsole(x: int, y: int) -> bool:
    """Test 3: LDConsole action API."""
    print(f"\n[3] LDConsole action")
    if not os.path.isfile(LDCONSOLE):
        print(f"    SKIP: ldconsole.exe not found at {LDCONSOLE}")
        return False

    cmd = [LDCONSOLE, "action", "--index", "0", "--key", "call.input", "--value", f"{x} {y}"]
    print(f"    Command: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    print(f"    Return code: {r.returncode}")
    print(f"    Stdout: {r.stdout.strip()}")
    if r.stderr.strip():
        print(f"    Stderr: {r.stderr.strip()}")
    time.sleep(0.5)
    screenshot("3_ldconsole")
    return r.returncode == 0


def test_adb_input_tap(x: int, y: int) -> bool:
    """Test 4: ADB input touchscreen tap."""
    print(f"\n[4] ADB input touchscreen tap")
    cmd = [ADB_EXE, "-s", ADB_DEVICE, "shell", "input", "touchscreen", "tap", str(x), str(y)]
    print(f"    Command: adb shell input touchscreen tap {x} {y}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    print(f"    Return code: {r.returncode}")
    time.sleep(0.5)
    screenshot("4_adb_tap")
    return r.returncode == 0


def test_sendevent(x: int, y: int) -> bool:
    """Test 5: Raw sendevent with correct axis interpolation."""
    print(f"\n[5] Raw sendevent (kernel bypass)")

    # Discover axis limits
    print("    Discovering digitizer axes...")
    r = subprocess.run(
        [ADB_EXE, "-s", ADB_DEVICE, "shell", "getevent", "-p"],
        capture_output=True, text=True, timeout=8,
    )

    import re
    x_max, y_max = 1279, 719
    device = "/dev/input/event2"

    current_dev = ""
    for line in r.stdout.splitlines():
        dm = re.match(r"^add device \d+:\s*(.+)", line)
        if dm:
            current_dev = dm.group(1).strip()
        am = re.match(r"\s+(0035|ABS_MT_POSITION_X)\s*:.*max\s+(\d+)", line)
        if am:
            x_max = int(am.group(2))
            device = current_dev
        am2 = re.match(r"\s+(0036|ABS_MT_POSITION_Y)\s*:.*max\s+(\d+)", line)
        if am2:
            y_max = int(am2.group(2))

    # Interpolation: display (x,y) -> digitizer (touch_x, touch_y)
    # Digitizer is rotated 90 degrees: X axis = display Y, Y axis = display X
    touch_x = int(y / 1280 * x_max)
    touch_y = int(x / 720 * y_max)

    print(f"    Device: {device}")
    print(f"    Axis X max: {x_max}, Axis Y max: {y_max}")
    print(f"    Display ({x},{y}) -> Touch ({touch_x},{touch_y})")

    d = device
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

    r1 = subprocess.run(
        [ADB_EXE, "-s", ADB_DEVICE, "shell", down],
        capture_output=True, text=True, timeout=5,
    )
    time.sleep(0.05)
    r2 = subprocess.run(
        [ADB_EXE, "-s", ADB_DEVICE, "shell", up],
        capture_output=True, text=True, timeout=5,
    )

    ok = r1.returncode == 0 and r2.returncode == 0
    print(f"    Down return: {r1.returncode}, Up return: {r2.returncode}")
    if r1.stderr.strip():
        print(f"    Stderr: {r1.stderr.strip()}")
    time.sleep(0.5)
    screenshot("5_sendevent")
    return ok


def main():
    target_x, target_y = get_target(sys.argv[1:])
    print("=" * 60)
    print("  CLICK INJECTION TEST SUITE - Project Titan v3")
    print("=" * 60)
    print(f"Target: ({target_x}, {target_y}) in Android 720x1280")
    print(f"ADB: {ADB_EXE}")
    print(f"Device: {ADB_DEVICE}")

    # Take baseline screenshot
    print("\n[0] Baseline screenshot BEFORE clicks")
    screenshot("0_baseline")

    # Find HWND
    hwnd = find_render_hwnd()
    if hwnd:
        print(f"\nLDPlayer RenderWindow HWND: {hwnd:#x}")
        crect = wintypes.RECT()
        _user32.GetClientRect(hwnd, ctypes.byref(crect))
        print(f"Client area: {crect.right}x{crect.bottom}")
    else:
        print("\nWARNING: LDPlayer RenderWindow HWND not found!")

    results = {}

    # Test each backend with 3-second pause between
    if hwnd:
        print("\n--- Testing Win32 backends (HWND found) ---")
        results["SendMessage"] = test_win32_sendmessage(hwnd, target_x, target_y)
        time.sleep(2)
        results["PostMessage"] = test_win32_postmessage(hwnd, target_x, target_y)
        time.sleep(2)
    else:
        results["SendMessage"] = False
        results["PostMessage"] = False

    results["LDConsole"] = test_ldconsole(target_x, target_y)
    time.sleep(2)

    results["ADB tap"] = test_adb_input_tap(target_x, target_y)
    time.sleep(2)

    results["sendevent"] = test_sendevent(target_x, target_y)

    # Summary
    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    for name, ok in results.items():
        status = "OK (sent)" if ok else "FAILED"
        print(f"  {name:20s}: {status}")

    print()
    print("NOTE: 'OK (sent)' means the command executed without error.")
    print("Check the saved screenshots in reports/ to verify which")
    print("backend actually registered a click in PPPoker/Unity.")
    print()
    print("Compare screenshots:")
    print("  0_baseline  = before any clicks")
    print("  1-5         = after each backend")
    print("If a screenshot shows a button pressed/highlighted, that backend works!")


if __name__ == "__main__":
    main()

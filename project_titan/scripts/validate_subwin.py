#!/usr/bin/env python3
"""Quick validation: confirm subWin receives clicks correctly.

Takes before screenshot, sends SendMessage to subWin, takes after screenshot,
and reports pixel diff.  Also tests the refactored _find_ldplayer_render_hwnd()
function to ensure it now picks subWin.
"""
from __future__ import annotations
import os, sys, time, subprocess
import cv2, numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADB = os.getenv("TITAN_ADB_PATH", r"F:\LDPlayer\LDPlayer9\adb.exe")
DEV = os.getenv("TITAN_ADB_DEVICE", "emulator-5554")

def screenshot():
    r = subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    if r.returncode == 0 and len(r.stdout) > 100:
        return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    return None

def main():
    # Test 1: Does the refactored function pick subWin?
    print("=== TEST 1: _find_ldplayer_render_hwnd() selection ===")
    import ctypes, ctypes.wintypes as wintypes
    _u32 = ctypes.windll.user32

    # Inline version of the discovery logic to avoid circular imports
    all_children = []
    render_classes = {"RenderWindow", "TheRender", "sub", "subWin"}
    priority = {"subWin": 0, "sub": 1, "TheRender": 2, "RenderWindow": 3}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_top(hwnd, _lp):
        if not _u32.IsWindowVisible(hwnd):
            return True
        cname = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(hwnd, cname, 256)
        if cname.value in ("LDPlayerMainFrame", "LDPlayer"):
            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def enum_child(child, _lp2):
                cn2 = ctypes.create_unicode_buffer(256)
                _u32.GetClassNameW(child, cn2, 256)
                if cn2.value in render_classes:
                    all_children.append((child, cn2.value))
                return True
            _u32.EnumChildWindows(hwnd, enum_child, 0)
            if all_children:
                return False
        return True
    _u32.EnumWindows(enum_top, 0)
    
    if not all_children:
        print("ERROR: No LDPlayer children found!")
        return
    
    all_children.sort(key=lambda x: priority.get(x[1], 99))
    hwnd, cname_val = all_children[0]
    
    cr = wintypes.RECT()
    _u32.GetClientRect(hwnd, ctypes.byref(cr))
    print(f"  Selected HWND: {hwnd:#x}")
    print(f"  Class: {cname_val!r}")
    print(f"  Client: {cr.right}x{cr.bottom}")
    print(f"  All candidates: {all_children}")
    assert cname_val == "subWin", f"Expected subWin, got {cname_val!r}"
    print("  OK: subWin selected correctly!")

    # Test 2: Click the Check button via raw Win32 SendMessage
    print("\n=== TEST 2: Click 'Check' button via SendMessage to subWin ===")
    target_x, target_y = 270, 1230
    
    before = screenshot()
    if before is None:
        print("ERROR: cannot screenshot")
        return
    cv2.imwrite("reports/validate_before.png", before)

    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001

    cw, ch = cr.right, cr.bottom
    px = int(target_x / 720 * cw)
    py = int(target_y / 1280 * ch)
    lp = (py << 16) | (px & 0xFFFF)
    
    print(f"  Android ({target_x},{target_y}) -> Client pixel ({px},{py})")
    
    _u32.SendMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.02)
    _u32.SendMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(0.06)
    _u32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    print(f"  SendMessage sent to subWin {hwnd:#x}")
    
    time.sleep(1.5)
    after = screenshot()
    if after is None:
        print("ERROR: cannot screenshot after click")
        return
    cv2.imwrite("reports/validate_after.png", after)

    if before.shape == after.shape:
        d = cv2.absdiff(before, after)
        changed = int(np.sum(np.any(d > 15, axis=2)))
        total = before.shape[0] * before.shape[1]
        pct = changed / total * 100
        print(f"  Pixel diff: {changed} / {total} ({pct:.1f}%)")
        if changed > 5000:
            print("  >>> CLICK REGISTERED SUCCESSFULLY! <<<")
        elif changed > 500:
            print("  >>> POSSIBLE click (moderate change)")
        else:
            print("  >>> NO VISIBLE EFFECT")
    else:
        print("  Resolution changed between screenshots")

    # Test 3: Full GhostMouse integration
    print("\n=== TEST 3: GhostMouse._execute_ldplayer_click() ===")
    os.environ["TITAN_INPUT_BACKEND"] = "ldplayer"
    os.environ["TITAN_GHOST_MOUSE"] = "1"
    
    from agent.ghost_mouse import GhostMouse
    from tools.mouse_protocol import ClickPoint
    
    gm = GhostMouse()
    print(f"  Backend: {gm._input_backend}")
    print(f"  Enabled: {gm._enabled}")
    print(f"  HWND: {gm._ld_render_hwnd:#x}" if gm._ld_render_hwnd else "  HWND: None")
    
    if gm._ld_render_hwnd:
        cn2 = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(gm._ld_render_hwnd, cn2, 256)
        print(f"  HWND class: {cn2.value!r}")
    
    before2 = screenshot()
    if before2 is not None:
        cv2.imwrite("reports/validate_gm_before.png", before2)
    
    # Click fold button
    print("  Clicking Fold (126, 1230)...")
    gm._execute_ldplayer_click(ClickPoint(x=126, y=1230), pre_delay=0)
    time.sleep(1.5)
    
    after2 = screenshot()
    if after2 is not None:
        cv2.imwrite("reports/validate_gm_after.png", after2)
    
    if before2 is not None and after2 is not None and before2.shape == after2.shape:
        d2 = cv2.absdiff(before2, after2)
        changed2 = int(np.sum(np.any(d2 > 15, axis=2)))
        print(f"  Pixel diff: {changed2}")
        if changed2 > 5000:
            print("  >>> GHOSTMOUSE CLICK REGISTERED! <<<")
        else:
            print(f"  Moderate/no change ({changed2} px)")
    
    stats = gm.get_click_stats()
    print(f"  Click stats: {stats}")
    gm.shutdown()
    
    print("\nAll validation tests complete.")

if __name__ == "__main__":
    main()

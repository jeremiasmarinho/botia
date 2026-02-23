"""LIVE click test with AttachThreadInput focus fix.

Usage: python debug_live_test.py [centre|call|fold|raise]
"""
import ctypes, ctypes.wintypes as wt, time, sys, subprocess, os
import numpy as np
from PIL import Image

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"

# ── Find LDPlayer ──
u = ctypes.windll.user32
k = ctypes.windll.kernel32

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

parents, renders = [], []
@ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
def enum_top(hwnd, _):
    if not u.IsWindowVisible(hwnd):
        return True
    cn = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, cn, 256)
    if cn.value == "LDPlayerMainFrame":
        parents.append(hwnd)
        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def enum_child(ch, _):
            cn2 = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(ch, cn2, 256)
            if cn2.value == "RenderWindow":
                renders.append(ch)
                return False
            return True
        u.EnumChildWindows(hwnd, enum_child, 0)
        return False if renders else True
    return True
u.EnumWindows(enum_top, 0)

if not renders:
    print("FAIL: RenderWindow not found"); sys.exit(1)

parent, render = parents[0], renders[0]
pt = POINT(0,0)
u.ClientToScreen(render, ctypes.byref(pt))
cr = wt.RECT()
u.GetClientRect(render, ctypes.byref(cr))
sl, st, cw, ch = pt.x, pt.y, cr.right, cr.bottom
print(f"RenderWindow: ({sl},{st}) {cw}x{ch}")

# ── Targets ──
ANDROID_W, ANDROID_H = 1080, 1920
targets = {
    "centre": (540, 960),
    "fold":   (189, 1830),
    "call":   (542, 1830),
    "raise":  (894, 1830),
}

action = sys.argv[1] if len(sys.argv) > 1 else "call"
if action not in targets:
    print(f"Unknown action: {action}. Use: {list(targets.keys())}")
    sys.exit(1)

ax, ay = targets[action]
sx = sl + int(ax * cw / ANDROID_W)
sy = st + int(ay * ch / ANDROID_H)
print(f"Action: {action} android=({ax},{ay}) screen=({sx},{sy})")

# ── Screenshot helper ──
def ss(name):
    r = subprocess.run([ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
                      capture_output=True, timeout=10)
    path = f"reports/{name}.png"
    os.makedirs("reports", exist_ok=True)
    with open(path, "wb") as f:
        f.write(r.stdout)
    return path

# ── Force foreground ──
def force_foreground(hwnd):
    fg = u.GetForegroundWindow()
    fg_t = u.GetWindowThreadProcessId(fg, None)
    my_t = k.GetCurrentThreadId()
    attached = False
    if fg_t != my_t:
        attached = bool(u.AttachThreadInput(my_t, fg_t, True))
    u.BringWindowToTop(hwnd)
    u.SetForegroundWindow(hwnd)
    if attached:
        u.AttachThreadInput(my_t, fg_t, False)
    time.sleep(0.20)
    # Verify
    fg2 = u.GetForegroundWindow()
    ok = fg2 == hwnd
    if not ok:
        u.ShowWindow(hwnd, 6)
        time.sleep(0.15)
        u.ShowWindow(hwnd, 9)
        time.sleep(0.25)
        fg3 = u.GetForegroundWindow()
        ok = fg3 == hwnd
    return ok

# ── Execute ──
import pyautogui
pyautogui.FAILSAFE = False

print("\n--- BEFORE ---")
p_before = ss("live_before")

# Force focus
print("Forcing foreground focus...")
ok = force_foreground(parent)
fg_title = ctypes.create_unicode_buffer(256)
u.GetWindowTextW(u.GetForegroundWindow(), fg_title, 256)
print(f"  Focus OK: {ok}, foreground: '{fg_title.value}'")

# Click
print(f"\nClicking ({sx},{sy})...")
pyautogui.click(sx, sy)
time.sleep(1.5)

print("\n--- AFTER ---")
p_after = ss("live_after")

# Compare
img1 = np.array(Image.open(p_before))[:,:,:3]
img2 = np.array(Image.open(p_after))[:,:,:3]
diff_mean = np.abs(img1.astype(int) - img2.astype(int)).mean()
print(f"\nDiff: mean={diff_mean:.2f}")

# Check button area specifically
btn_before = img1[1780:1900, :, :3]
btn_after = img2[1780:1900, :, :3]
btn_diff = np.abs(btn_before.astype(int) - btn_after.astype(int)).mean()
print(f"Button area diff: {btn_diff:.2f}")

if diff_mean > 1.0:
    print("\n*** CHANGE DETECTED - Click likely registered! ***")
    # Find where the biggest changes are
    diff_img = np.abs(img1.astype(float) - img2.astype(float))
    for gi in range(4):
        for gj in range(4):
            y0, y1 = gi * ANDROID_H // 4, (gi+1) * ANDROID_H // 4
            x0, x1 = gj * ANDROID_W // 4, (gj+1) * ANDROID_W // 4
            r_diff = diff_img[y0:y1, x0:x1].mean()
            if r_diff > 3.0:
                print(f"  Changed: y=[{y0}-{y1}] x=[{x0}-{x1}] diff={r_diff:.1f}")
else:
    print("\n*** NO CHANGE - Click did NOT register ***")

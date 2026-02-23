"""Click with pyautogui while getevent monitors touch input."""
import ctypes, ctypes.wintypes as wt, time, subprocess, os

u = ctypes.windll.user32

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

# Find RenderWindow
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
parent, render = parents[0], renders[0]

# Get coords
pt = POINT(0, 0)
u.ClientToScreen(render, ctypes.byref(pt))
cr = wt.RECT()
u.GetClientRect(render, ctypes.byref(cr))
sl, st, cw, ch = pt.x, pt.y, cr.right, cr.bottom
cx, cy = sl + cw//2, st + ch//2

print(f"RenderWindow: ({sl},{st}) {cw}x{ch}")
print(f"Centre: ({cx},{cy})")

# Wait for getevent to start
time.sleep(1)
print("\nGetevent monitoring started. Clicking now...")

# Focus
u.SetForegroundWindow(parent)
time.sleep(0.5)

import pyautogui
pyautogui.FAILSAFE = False

# Click 1: centre
print(f"\nClick 1: centre ({cx},{cy})")
pyautogui.click(cx, cy)
time.sleep(2)

# Click 2: top area (safe)
tx, ty = sl + 50, st + 50
print(f"\nClick 2: top-left ({tx},{ty})")
pyautogui.click(tx, ty)
time.sleep(2)

# Click 3: bottom area (where buttons would be)
bx = sl + int(542 * cw / 1080)
by = st + int(1830 * ch / 1920)
print(f"\nClick 3: call area ({bx},{by})")
pyautogui.click(bx, by)
time.sleep(2)

print("\nClicks done. Reading getevent log...")

# Read the getevent log
time.sleep(1)
try:
    with open("reports/getevent_log.txt", "r") as f:
        lines = f.readlines()
    print(f"\nGetevent captured {len(lines)} lines:")
    for line in lines[-30:]:  # last 30 lines
        print(f"  {line.rstrip()}")
except Exception as e:
    print(f"Error reading log: {e}")

# Also try direct getevent with timeout
print("\n--- Quick getevent check ---")
try:
    r = subprocess.run(
        [r"F:\LDPlayer\LDPlayer9\adb.exe", "-s", "emulator-5554",
         "shell", "cat", "/proc/bus/input/devices"],
        capture_output=True, text=True, timeout=5
    )
    print("Input devices:")
    for line in r.stdout.split('\n'):
        if 'Name' in line or 'Handlers' in line:
            print(f"  {line.strip()}")
except Exception as e:
    print(f"Error: {e}")

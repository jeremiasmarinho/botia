"""Debug click: verify focus + coordinates + screenshot before/after."""
import os, sys, time, ctypes, ctypes.wintypes as wt

os.environ["TITAN_GHOST_MOUSE"] = "1"
os.environ["TITAN_INPUT_BACKEND"] = "ldplayer"
os.environ["TITAN_ANDROID_W"] = "1080"
os.environ["TITAN_ANDROID_H"] = "1920"
os.environ["TITAN_ADB_PATH"] = r"F:\LDPlayer\LDPlayer9\adb.exe"
os.environ["TITAN_ADB_DEVICE"] = "emulator-5554"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pyautogui
pyautogui.FAILSAFE = False

u = ctypes.windll.user32

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

# Step 1: Find LDPlayer RenderWindow
result = []

@ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
def enum_top(hwnd, _lp):
    if not u.IsWindowVisible(hwnd):
        return True
    cname = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, cname, 256)
    if cname.value == "LDPlayerMainFrame":
        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def enum_child(child, _lp2):
            cn2 = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(child, cn2, 256)
            if cn2.value == "RenderWindow":
                result.append(child)
                return False
            return True
        u.EnumChildWindows(hwnd, enum_child, 0)
        if result:
            return False
    return True

u.EnumWindows(enum_top, 0)
if not result:
    print("FAIL: RenderWindow not found")
    sys.exit(1)

hwnd = result[0]
parent = u.GetParent(hwnd) or hwnd

# Get geometry
pt = POINT(0, 0)
u.ClientToScreen(hwnd, ctypes.byref(pt))
crect = wt.RECT()
u.GetClientRect(hwnd, ctypes.byref(crect))
sl, st, cw, ch = pt.x, pt.y, crect.right, crect.bottom

print(f"RenderWindow: hwnd=0x{hwnd:08X}")
print(f"  Screen pos: ({sl}, {st})")
print(f"  Client size: {cw} x {ch}")
print(f"  Bottom edge: y={st + ch}")

# Target: Call button
ax, ay = 542, 1830
aw, ah = 1080, 1920
sx = sl + int(ax * cw / aw)
sy = st + int(ay * ch / ah)
print(f"\nCall button: android=({ax},{ay}) -> screen=({sx},{sy})")

# Centre of render
cx = sl + cw // 2
cy = st + ch // 2
print(f"Centre: screen=({cx},{cy})")

# Check current foreground window
fg = u.GetForegroundWindow()
fg_title = ctypes.create_unicode_buffer(256)
u.GetWindowTextW(fg, fg_title, 256)
print(f"\nCurrent foreground: '{fg_title.value}' hwnd=0x{fg:08X}")

# Step 2: Take screenshot BEFORE
import subprocess
def take_ss(name):
    r = subprocess.run(
        [r"F:\LDPlayer\LDPlayer9\adb.exe", "-s", "emulator-5554",
         "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10
    )
    if r.returncode == 0 and len(r.stdout) > 100:
        path = f"reports/{name}.png"
        os.makedirs("reports", exist_ok=True)
        with open(path, "wb") as f:
            f.write(r.stdout)
        print(f"  Screenshot: {path} ({len(r.stdout):,} bytes)")

print("\n--- BEFORE click ---")
take_ss("debug_before")

# Step 3: Focus + Click
print(f"\n--- Clicking at screen=({sx},{sy}) ---")

# Focus on LDPlayer
u.SetForegroundWindow(parent)
time.sleep(0.3)

# Verify foreground changed
fg2 = u.GetForegroundWindow()
fg2_title = ctypes.create_unicode_buffer(256)
u.GetWindowTextW(fg2, fg2_title, 256)
print(f"  Foreground after SetForegroundWindow: '{fg2_title.value}'")

# Focus click on centre
print(f"  Focus-click centre: ({cx},{cy})")
pyautogui.click(cx, cy)
time.sleep(0.4)

# Now check mouse position
mx, my = pyautogui.position()
print(f"  Mouse pos after focus click: ({mx},{my})")

# Click the actual target
print(f"  Target click: ({sx},{sy})")
pyautogui.click(sx, sy)
time.sleep(0.3)

mx2, my2 = pyautogui.position()
print(f"  Mouse pos after target click: ({mx2},{my2})")

# Step 4: Screenshot AFTER
time.sleep(1.0)
print("\n--- AFTER click ---")
take_ss("debug_after")

print("\nDone! Compare reports/debug_before.png and reports/debug_after.png")

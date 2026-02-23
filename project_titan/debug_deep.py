"""Deep window analysis + try every possible click vector."""
import ctypes, ctypes.wintypes as wt, time, subprocess, sys

u = ctypes.windll.user32
k = ctypes.windll.kernel32

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

# Find windows
parents, renders = [], []
all_wins = {}

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
            wr = wt.RECT()
            u.GetWindowRect(ch, ctypes.byref(wr))
            style = u.GetWindowLongW(ch, -16)
            exstyle = u.GetWindowLongW(ch, -20)
            vis = u.IsWindowVisible(ch)
            all_wins[ch] = {
                'class': cn2.value, 'vis': vis,
                'rect': (wr.left, wr.top, wr.right, wr.bottom),
                'style': style, 'exstyle': exstyle
            }
            if cn2.value == "RenderWindow":
                renders.append(ch)
            return True
        u.EnumChildWindows(hwnd, enum_child, 0)
        return False if renders else True
    return True
u.EnumWindows(enum_top, 0)

parent = parents[0]
render = renders[0]

# Show window hierarchy with Z-order
print("=== LDPlayer Window Hierarchy (Z-order) ===")
# GetWindow with GW_CHILD then GW_HWNDNEXT walks in Z-order
GW_CHILD = 5
GW_HWNDNEXT = 2
child = u.GetWindow(parent, GW_CHILD)
z_index = 0
while child:
    cn = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(child, cn, 256)
    wr = wt.RECT()
    u.GetWindowRect(child, ctypes.byref(wr))
    vis = u.IsWindowVisible(child)
    exstyle = u.GetWindowLongW(child, -20)

    WS_EX_TRANSPARENT = 0x20
    WS_EX_LAYERED = 0x80000
    WS_EX_NOACTIVATE = 0x08000000
    flags = []
    if exstyle & WS_EX_TRANSPARENT: flags.append("TRANSPARENT")
    if exstyle & WS_EX_LAYERED: flags.append("LAYERED")
    if exstyle & WS_EX_NOACTIVATE: flags.append("NOACTIVATE")

    size_w = wr.right - wr.left
    size_h = wr.bottom - wr.top
    marker = " <<<" if child == render else ""
    print(f"  Z{z_index}: 0x{child:08X} class='{cn.value}' "
          f"rect=({wr.left},{wr.top}) {size_w}x{size_h} "
          f"vis={vis} exstyle=0x{exstyle:08X} {' '.join(flags)}{marker}")

    z_index += 1
    child = u.GetWindow(child, GW_HWNDNEXT)

# Check if subWin is ABOVE RenderWindow in Z-order
print(f"\nRenderWindow: 0x{render:08X}")

# Check RealChildWindowFromPoint — what window is at the click point?
pt_c = POINT(0, 0)
u.ClientToScreen(render, ctypes.byref(pt_c))
cr = wt.RECT()
u.GetClientRect(render, ctypes.byref(cr))
sl, st, cw, ch = pt_c.x, pt_c.y, cr.right, cr.bottom

# Check what's at various points from PARENT's perspective
print("\n=== RealChildWindowFromPoint / ChildWindowFromPoint ===")
for name, (x, y) in [("centre", (sl+cw//2, st+ch//2)), 
                       ("call", (sl+int(542*cw/1080), st+int(1830*ch/1920)))]:
    # Convert to parent client coords
    pt_parent = POINT(x, y)
    u.ScreenToClient(parent, ctypes.byref(pt_parent))
    
    # RealChildWindowFromPoint - skips transparent windows
    real_child = u.RealChildWindowFromPoint(parent, pt_parent)
    cn_r = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(real_child, cn_r, 256)
    
    # ChildWindowFromPoint
    child_res = u.ChildWindowFromPoint(parent, pt_parent)
    cn_c = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(child_res, cn_c, 256)
    
    # WindowFromPoint (global)
    wfp = u.WindowFromPoint(POINT(x, y))
    cn_w = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(wfp, cn_w, 256)
    
    print(f"  {name:8s} screen=({x},{y}):")
    print(f"    WindowFromPoint:          0x{wfp:08X} class='{cn_w.value}'")
    print(f"    ChildWindowFromPoint:     0x{child_res:08X} class='{cn_c.value}'")
    print(f"    RealChildWindowFromPoint: 0x{real_child:08X} class='{cn_r.value}'")

# === Check if RenderWindow has RawInput registered ===
print("\n=== RawInput check (RAWINPUTDEVICE) ===")
# We can't directly check another window's RawInput registration from outside
# But we can check if the window procedure handles WM_INPUT
# Let's check what messages the window responds to
WM_INPUT = 0x00FF
GWL_WNDPROC = -4
proc = u.GetWindowLongPtrW(render, GWL_WNDPROC)
print(f"  RenderWindow WndProc: 0x{proc:016X}")

# === Try clicking DIRECTLY with sendevent but to ALL devices ===
ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"

print("\n=== Check ALL input devices capabilities ===")
for dev in range(5):
    r = subprocess.run([ADB, "-s", DEVICE, "shell", "getevent", "-p", f"/dev/input/event{dev}"],
                      capture_output=True, text=True, timeout=5)
    if "ABS" in r.stdout or "touch" in r.stdout.lower():
        print(f"  event{dev}: HAS ABS or touch!")
        # Show relevant lines
        for line in r.stdout.split('\n'):
            if 'ABS' in line or 'name' in line.lower():
                print(f"    {line.strip()}")

# === Try SetWindowPos to put RenderWindow on top ===
print("\n=== Try putting RenderWindow on top ===")
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001  
HWND_TOP = 0
# Before clicking, ensure RenderWindow is the topmost child
u.SetWindowPos(render, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)

# Also try SetFocus on both parent and render
print("\n=== Try SetFocus combinations ===")
# Attach to LDPlayer thread
ld_thread = u.GetWindowThreadProcessId(parent, None)
my_thread = k.GetCurrentThreadId()
att = u.AttachThreadInput(my_thread, ld_thread, True)
print(f"  Attached to LDPlayer thread: {att}")

u.SetForegroundWindow(parent)
time.sleep(0.1)
r1 = u.SetFocus(parent)
print(f"  SetFocus(parent): {r1}")
r2 = u.SetFocus(render)
print(f"  SetFocus(render): {r2}")

# Now click with raw mouse_event
print(f"\n=== Clicking centre ({sl+cw//2},{st+ch//2}) with mouse_event ===")
target_x, target_y = sl+cw//2, st+ch//2
u.SetCursorPos(target_x, target_y)
time.sleep(0.1)

# Take screenshot before
r = subprocess.run([ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
                  capture_output=True, timeout=10)
with open("reports/deep_before.png", "wb") as f:
    f.write(r.stdout)
print(f"  Before: {len(r.stdout):,} bytes")

# Click with mouse_event
u.mouse_event(0x0002, 0, 0, 0, 0)  # DOWN
time.sleep(0.15)
u.mouse_event(0x0004, 0, 0, 0, 0)  # UP
time.sleep(1.0)

# Screenshot after
r2 = subprocess.run([ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
                   capture_output=True, timeout=10)
with open("reports/deep_after.png", "wb") as f:
    f.write(r2.stdout)
print(f"  After: {len(r2.stdout):,} bytes")

u.AttachThreadInput(my_thread, ld_thread, False)

# Compare
import numpy as np
from PIL import Image
img1 = np.array(Image.open("reports/deep_before.png"))[:,:,:3]
img2 = np.array(Image.open("reports/deep_after.png"))[:,:,:3]
diff = np.abs(img1.astype(int) - img2.astype(int)).mean()
print(f"  Diff: {diff:.2f}")

if diff > 1.0:
    print("  *** CLICK REGISTERED! ***")
else:
    print("  *** Click did NOT register ***")
    print("\n  CONCLUSION: RenderWindow is not responding to synthetic input.")
    print("  LDPlayer may use DirectInput/RawInput/custom hook.")
    print("  Next try: use Scrcpy for input injection.")

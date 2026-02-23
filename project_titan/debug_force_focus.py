"""Force foreground focus on LDPlayer using AttachThreadInput trick."""
import ctypes, ctypes.wintypes as wt, time, sys

u = ctypes.windll.user32
k = ctypes.windll.kernel32

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

# Find LDPlayer
parents = []
renders = []
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
parent = parents[0]
render = renders[0]

# Identify current foreground
fg_before = u.GetForegroundWindow()
fg_title = ctypes.create_unicode_buffer(256)
u.GetWindowTextW(fg_before, fg_title, 256)
fg_class = ctypes.create_unicode_buffer(256)
u.GetClassNameW(fg_before, fg_class, 256)
print(f"Current foreground: 0x{fg_before:08X} class='{fg_class.value}' title='{fg_title.value}'")

# === FORCE FOREGROUND using AttachThreadInput ===
print("\n--- Forcing foreground focus on LDPlayer ---")

# Get thread IDs
fg_thread = u.GetWindowThreadProcessId(fg_before, None)
target_thread = u.GetWindowThreadProcessId(parent, None)
my_thread = k.GetCurrentThreadId()

print(f"  My thread: {my_thread}")
print(f"  Foreground thread: {fg_thread}")
print(f"  LDPlayer thread: {target_thread}")

# Attach to foreground thread
r1 = u.AttachThreadInput(my_thread, fg_thread, True)
print(f"  AttachThreadInput(me, fg): {r1}")

# Now set foreground
r2 = u.BringWindowToTop(parent)
print(f"  BringWindowToTop: {r2}")

r3 = u.SetForegroundWindow(parent)
print(f"  SetForegroundWindow: {r3}")

# Also try ShowWindow
u.ShowWindow(parent, 5)  # SW_SHOW
u.SetFocus(render)

# Detach
u.AttachThreadInput(my_thread, fg_thread, False)

time.sleep(0.5)

# Verify foreground
fg_after = u.GetForegroundWindow()
fg_title2 = ctypes.create_unicode_buffer(256)
u.GetWindowTextW(fg_after, fg_title2, 256)
print(f"\n  Foreground NOW: 0x{fg_after:08X} title='{fg_title2.value}'")
is_ld = fg_after == parent
print(f"  Is LDPlayer foreground? {is_ld}")

if not is_ld:
    print("\n  Trying alternative: minimize/restore")
    # Minimize then restore to force focus
    u.ShowWindow(parent, 6)  # SW_MINIMIZE
    time.sleep(0.3)
    u.ShowWindow(parent, 9)  # SW_RESTORE
    time.sleep(0.5)
    fg_after2 = u.GetForegroundWindow()
    fg_title3 = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(fg_after2, fg_title3, 256)
    print(f"  After restore: 0x{fg_after2:08X} title='{fg_title3.value}'")
    is_ld = fg_after2 == parent

# Get render coords
pt = POINT(0, 0)
u.ClientToScreen(render, ctypes.byref(pt))
cr = wt.RECT()
u.GetClientRect(render, ctypes.byref(cr))
sl, st, cw, ch = pt.x, pt.y, cr.right, cr.bottom
cx, cy = sl + cw//2, st + ch//2
sx = sl + int(542 * cw / 1080)
sy = st + int(1830 * ch / 1920)

print(f"\nRenderWindow: ({sl},{st}) {cw}x{ch}")
print(f"Centre: ({cx},{cy}), Call: ({sx},{sy})")

if is_ld or True:  # try regardless
    import pyautogui
    pyautogui.FAILSAFE = False
    
    # Verify LDPlayer is foreground right before click
    fg_now = u.GetForegroundWindow()
    print(f"\nRight before click - foreground: 0x{fg_now:08X}")
    
    # Click IMMEDIATELY - no other operations between focus and click
    print(f"Clicking centre ({cx},{cy})...")
    pyautogui.click(cx, cy)
    time.sleep(1.0)
    
    # Take screenshot to check
    import subprocess
    r = subprocess.run(
        [r"F:\LDPlayer\LDPlayer9\adb.exe", "-s", "emulator-5554",
         "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10
    )
    if r.returncode == 0 and len(r.stdout) > 100:
        with open("reports/force_focus_after.png", "wb") as f:
            f.write(r.stdout)
        print(f"Screenshot: {len(r.stdout):,} bytes")
    
    # Now try clicking the call button position
    print(f"\nClicking call position ({sx},{sy})...")
    
    # Re-force focus
    u.AttachThreadInput(my_thread, u.GetWindowThreadProcessId(u.GetForegroundWindow(), None), True)
    u.SetForegroundWindow(parent)
    u.AttachThreadInput(my_thread, u.GetWindowThreadProcessId(u.GetForegroundWindow(), None), False)
    time.sleep(0.3)
    
    pyautogui.click(sx, sy)
    time.sleep(1.0)
    
    r2 = subprocess.run(
        [r"F:\LDPlayer\LDPlayer9\adb.exe", "-s", "emulator-5554",
         "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10
    )
    if r2.returncode == 0 and len(r2.stdout) > 100:
        with open("reports/force_focus_call.png", "wb") as f:
            f.write(r2.stdout)
        print(f"Screenshot after call: {len(r2.stdout):,} bytes")

print("\nDone!")

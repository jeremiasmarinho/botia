"""Diagnose DPI scaling and test raw SendInput click."""
import ctypes, ctypes.wintypes as wt, time, struct, sys, os

# ===== DPI AWARENESS FIRST =====
try:
    # Per-monitor DPI awareness v2
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    dpi_mode = "PerMonitorV2"
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        dpi_mode = "SystemAware"
    except Exception:
        dpi_mode = "Unaware"

print(f"DPI Mode: {dpi_mode}")

u = ctypes.windll.user32

# Get system DPI
try:
    hdc = u.GetDC(0)
    dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
    dpi_y = ctypes.windll.gdi32.GetDeviceCaps(hdc, 90)  # LOGPIXELSY
    u.ReleaseDC(0, hdc)
    scale = dpi_x / 96.0
    print(f"System DPI: {dpi_x}x{dpi_y} (scale={scale:.0%})")
except Exception as e:
    # Fallback: use DpiForSystem
    try:
        dpi_x = u.GetDpiForSystem()
        scale = dpi_x / 96.0
        print(f"System DPI: {dpi_x} (scale={scale:.0%})")
    except Exception:
        scale = 1.0
        dpi_x = 96
        print(f"Could not get DPI, assuming 100%: {e}")

# Get screen resolution
sm_cx = u.GetSystemMetrics(0)  # SM_CXSCREEN
sm_cy = u.GetSystemMetrics(1)  # SM_CYSCREEN
print(f"Screen metrics: {sm_cx}x{sm_cy}")

# Find RenderWindow with DPI-aware coordinates
class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

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
parent = u.GetParent(hwnd)

# DPI-aware coordinates
pt = POINT(0, 0)
u.ClientToScreen(hwnd, ctypes.byref(pt))
crect = wt.RECT()
u.GetClientRect(hwnd, ctypes.byref(crect))
wrect = wt.RECT()
u.GetWindowRect(hwnd, ctypes.byref(wrect))

print(f"\nRenderWindow: hwnd=0x{hwnd:08X} parent=0x{parent:08X}")
print(f"  ClientToScreen: ({pt.x}, {pt.y})")
print(f"  GetClientRect: {crect.right}x{crect.bottom}")
print(f"  GetWindowRect: ({wrect.left},{wrect.top})-({wrect.right},{wrect.bottom})")
print(f"  WindowRect size: {wrect.right-wrect.left}x{wrect.bottom-wrect.top}")

# Compare WITHOUT DPI awareness 
# (we already set it, so these should be physical)
cw, ch = crect.right, crect.bottom
sl, st = pt.x, pt.y

# Target: center of window (safe test click)
cx, cy = sl + cw // 2, st + ch // 2
print(f"  Centre screen: ({cx},{cy})")

# Target: call button
ax, ay = 542, 1830
sx = sl + int(ax * cw / 1080)
sy = st + int(ay * ch / 1920) 
print(f"  Call button screen: ({sx},{sy})")

# ===== TEST 1: Get actual cursor position after move =====
print("\n--- TEST 1: Verify SetCursorPos reaches target ---")
u.SetCursorPos(cx, cy)
time.sleep(0.2)
actual = POINT()
u.GetCursorPos(ctypes.byref(actual))
print(f"  Target: ({cx},{cy})")
print(f"  Actual: ({actual.x},{actual.y})")
print(f"  Match: {actual.x == cx and actual.y == cy}")

# Check what window is under cursor
hwnd_at = u.WindowFromPoint(POINT(cx, cy))
cn = ctypes.create_unicode_buffer(256)
u.GetClassNameW(hwnd_at, cn, 256)
print(f"  Window under cursor: class={cn.value} hwnd=0x{hwnd_at:08X}")

# ===== TEST 2: Use SendInput directly =====
print("\n--- TEST 2: Raw SendInput click at centre ---")

# SendInput structures
MOUSEEVENTF_MOVE     = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

INPUT_MOUSE = 0

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', ctypes.c_long),
        ('dy', ctypes.c_long),
        ('mouseData', ctypes.c_ulong),
        ('dwFlags', ctypes.c_ulong),
        ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [('mi', MOUSEINPUT)]
    _fields_ = [
        ('type', ctypes.c_ulong),
        ('i', _I),
    ]

def send_click(x, y):
    """Send a click using SendInput at absolute screen coordinates."""
    # Convert to absolute coordinates (0-65535 range)
    abs_x = int(x * 65535 / (sm_cx - 1))  
    abs_y = int(y * 65535 / (sm_cy - 1))
    
    # Move
    inp_move = INPUT()
    inp_move.type = INPUT_MOUSE
    inp_move.i.mi.dx = abs_x
    inp_move.i.mi.dy = abs_y
    inp_move.i.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
    inp_move.i.mi.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
    
    n = u.SendInput(1, ctypes.byref(inp_move), ctypes.sizeof(INPUT))
    print(f"  Move to abs({abs_x},{abs_y}) screen({x},{y}): sent={n}")
    time.sleep(0.1)
    
    # Down
    inp_down = INPUT()
    inp_down.type = INPUT_MOUSE  
    inp_down.i.mi.dx = abs_x
    inp_down.i.mi.dy = abs_y
    inp_down.i.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE
    inp_down.i.mi.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
    
    n = u.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    print(f"  MouseDown: sent={n}")
    time.sleep(0.08)
    
    # Up
    inp_up = INPUT()
    inp_up.type = INPUT_MOUSE
    inp_up.i.mi.dx = abs_x
    inp_up.i.mi.dy = abs_y
    inp_up.i.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE
    inp_up.i.mi.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
    
    n = u.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
    print(f"  MouseUp: sent={n}")

# Focus LDPlayer first
u.SetForegroundWindow(parent)
time.sleep(0.5)

fg = u.GetForegroundWindow()
fg_t = ctypes.create_unicode_buffer(256)
u.GetWindowTextW(fg, fg_t, 256)
print(f"  Foreground: '{fg_t.value}'")

# Take screenshot before
import subprocess
def take_ss(name):
    r = subprocess.run(
        [r"F:\LDPlayer\LDPlayer9\adb.exe", "-s", "emulator-5554",
         "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10
    )
    if r.returncode == 0 and len(r.stdout) > 100:
        path = f"reports/{name}.png"
        with open(path, "wb") as f:
            f.write(r.stdout)
        print(f"  Screenshot: {path} ({len(r.stdout):,} bytes)")

take_ss("dpi_before")

# Click centre of render window
print(f"\n  Clicking centre ({cx},{cy})...")
send_click(cx, cy)
time.sleep(1.0)
take_ss("dpi_after_centre")

# Click where call button would be
print(f"\n  Clicking call ({sx},{sy})...")
send_click(sx, sy) 
time.sleep(1.0)
take_ss("dpi_after_call")

print("\n--- Summary ---")
print(f"DPI scale: {scale:.0%}")
print(f"Screen: {sm_cx}x{sm_cy}")
print(f"RenderWindow: {cw}x{ch} at ({sl},{st})")
print(f"Centre: ({cx},{cy})")
print(f"Call: ({sx},{sy})")

# ===== TEST 3: Try pyautogui with DPI already set =====
print("\n--- TEST 3: pyautogui.click with DPI awareness already set ---")
import pyautogui
pyautogui.FAILSAFE = False

# Move to center and click
pyautogui.moveTo(cx, cy)
time.sleep(0.2)
pos = pyautogui.position()
print(f"  pyautogui target: ({cx},{cy})")
print(f"  pyautogui.position(): ({pos.x},{pos.y})")
print(f"  Match: {pos.x == cx and pos.y == cy}")

# If they don't match, there's a DPI issue
if pos.x != cx or pos.y != cy:
    print(f"\n  !!! DPI MISMATCH DETECTED !!!")
    print(f"  Ratio: x={pos.x/cx:.3f} y={pos.y/cy:.3f}")
    # pyautogui might be calling SetProcessDPIAware itself, overriding our setting
    print(f"  pyautogui likely uses different DPI context")

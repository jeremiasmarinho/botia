"""Minimal click test: mouse_event directly (what pyautogui actually uses)."""
import ctypes, ctypes.wintypes as wt, time, sys

u = ctypes.windll.user32

# DPI aware
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except: pass

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

# Find windows
result = []
@ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
def enum_top(hwnd, _):
    if not u.IsWindowVisible(hwnd):
        return True
    cn = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, cn, 256)
    if cn.value == "LDPlayerMainFrame":
        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def enum_child(ch, _):
            cn2 = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(ch, cn2, 256)
            if cn2.value == "RenderWindow":
                result.append(ch)
                return False
            return True
        u.EnumChildWindows(hwnd, enum_child, 0)
        return False if result else True
    return True

u.EnumWindows(enum_top, 0)
if not result:
    print("RenderWindow not found"); sys.exit(1)

hwnd = result[0]
parent = u.GetParent(hwnd)

# Get coords
pt = POINT(0,0)
u.ClientToScreen(hwnd, ctypes.byref(pt))
cr = wt.RECT()
u.GetClientRect(hwnd, ctypes.byref(cr))
sl, st, cw, ch = pt.x, pt.y, cr.right, cr.bottom

print(f"RenderWindow at ({sl},{st}) size {cw}x{ch}")

# ----- Enumerate ALL child windows to find overlays -----
print("\n--- ALL child windows of LDPlayerMainFrame ---")
all_children = []
@ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
def enum_all_children(child, _):
    cn = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(child, cn, 256)
    title = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(child, title, 256)
    wr = wt.RECT()
    u.GetWindowRect(child, ctypes.byref(wr))
    vis = u.IsWindowVisible(child)
    style = u.GetWindowLongW(child, -16)  # GWL_STYLE
    exstyle = u.GetWindowLongW(child, -20)  # GWL_EXSTYLE
    all_children.append(child)
    print(f"  hwnd=0x{child:08X} class='{cn.value}' title='{title.value}' "
          f"rect=({wr.left},{wr.top},{wr.right},{wr.bottom}) "
          f"vis={vis} style=0x{style:08X} exstyle=0x{exstyle:08X}")
    return True

u.EnumChildWindows(parent, enum_all_children, 0)

# Check RenderWindow style
style = u.GetWindowLongW(hwnd, -16)
exstyle = u.GetWindowLongW(hwnd, -20)
print(f"\nRenderWindow style=0x{style:08X} exstyle=0x{exstyle:08X}")
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
if exstyle & WS_EX_TRANSPARENT:
    print("  !! WS_EX_TRANSPARENT - input passes through!")
if exstyle & WS_EX_LAYERED:
    print("  !! WS_EX_LAYERED")

# Check WindowFromPoint at multiple locations
print("\n--- WindowFromPoint checks ---")
test_points = [
    ("centre", sl + cw//2, st + ch//2),
    ("call_btn", sl + int(542*cw/1080), st + int(1830*ch/1920)),
    ("top_left", sl + 10, st + 10),
    ("bottom_right", sl + cw - 10, st + ch - 10),
]
for name, x, y in test_points:
    h = u.WindowFromPoint(POINT(x, y))
    cn = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(h, cn, 256)
    print(f"  {name:15s} ({x:4d},{y:4d}) -> class={cn.value} hwnd=0x{h:08X}")

# ----- Method 1: mouse_event (what pyautogui uses) -----
print("\n=== Method 1: mouse_event (pyautogui's method) ===")
target_x = sl + cw // 2
target_y = st + ch // 2
print(f"Target: ({target_x},{target_y})")

u.SetForegroundWindow(parent)
time.sleep(0.5)

# Move cursor
u.SetCursorPos(target_x, target_y)
time.sleep(0.2)

# Verify
actual = POINT()
u.GetCursorPos(ctypes.byref(actual))
print(f"Cursor at: ({actual.x},{actual.y})")

# Click using mouse_event
print("mouse_event DOWN...")
u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
time.sleep(0.15)
print("mouse_event UP...")
u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
time.sleep(0.5)

print("\n=== Method 2: PostMessage WM_LBUTTONDOWN/UP to RenderWindow ===")
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
MK_LBUTTON = 0x0001

# Client coordinates for the click
client_x = cw // 2
client_y = ch // 2
lParam = client_y << 16 | client_x

print(f"PostMessage to hwnd=0x{hwnd:08X} client=({client_x},{client_y})")
r1 = u.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lParam)
time.sleep(0.1)
r2 = u.PostMessageW(hwnd, WM_LBUTTONUP, 0, lParam)
print(f"PostMessage results: down={r1} up={r2}")
time.sleep(0.5)

print("\n=== Method 3: SendMessage WM_LBUTTONDOWN/UP to RenderWindow ===")
r3 = u.SendMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lParam)
time.sleep(0.1)
r4 = u.SendMessageW(hwnd, WM_LBUTTONUP, 0, lParam)
print(f"SendMessage results: down={r3} up={r4}")
time.sleep(0.5)

# ----- Try clicking subWin instead -----
print("\n=== Method 4: Try clicking other child windows ===")
for child in all_children:
    cn = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(child, cn, 256)
    if cn.value not in ("RenderWindow",):
        cr2 = wt.RECT()
        u.GetWindowRect(child, ctypes.byref(cr2))
        if cr2.right - cr2.left > 100 and cr2.bottom - cr2.top > 100:
            # Try PostMessage to this window
            cw2, ch2 = cr2.right - cr2.left, cr2.bottom - cr2.top
            cx2, cy2 = cw2 // 2, ch2 // 2
            lp2 = cy2 << 16 | cx2
            print(f"  PostMessage to {cn.value} hwnd=0x{child:08X} ({cw2}x{ch2})")
            u.PostMessageW(child, WM_LBUTTONDOWN, MK_LBUTTON, lp2)
            time.sleep(0.05)
            u.PostMessageW(child, WM_LBUTTONUP, 0, lp2)

print("\nDone. Check the LDPlayer window to see if any click registered.")

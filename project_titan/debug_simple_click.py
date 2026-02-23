"""Dead simple click test - NO DPI awareness, NO tricks.
Just pyautogui.click like the 3/3 confirmed test.
Observe the LDPlayer window carefully when running."""
import ctypes, time, sys

u = ctypes.windll.user32

# DO NOT set DPI awareness - keep it like before

# Find parent LDPlayer window
import ctypes.wintypes as wt
class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

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

# Get position WITHOUT DPI awareness
pt = POINT(0, 0)
u.ClientToScreen(render, ctypes.byref(pt))
cr = wt.RECT()
u.GetClientRect(render, ctypes.byref(cr))
sl, st, cw, ch = pt.x, pt.y, cr.right, cr.bottom
print(f"RenderWindow: ({sl},{st}) {cw}x{ch}")

# Map call button
sx = sl + int(542 * cw / 1080)
sy = st + int(1830 * ch / 1920)
print(f"Call button screen: ({sx},{sy})")

# Center of window
cx = sl + cw // 2
cy = st + ch // 2
print(f"Centre: ({cx},{cy})")

# Step 1: SetForegroundWindow on PARENT
print(f"\n1. SetForegroundWindow(parent=0x{parent:08X})")
u.SetForegroundWindow(parent)
time.sleep(1.0)  # generous wait

fg = u.GetForegroundWindow()
print(f"   Foreground now: 0x{fg:08X}")

# Step 2: Move cursor to target
print(f"\n2. Moving cursor to ({sx},{sy})")
u.SetCursorPos(sx, sy)
time.sleep(0.3)

actual = POINT()
u.GetCursorPos(ctypes.byref(actual))
print(f"   Cursor actual: ({actual.x},{actual.y})")

# Step 3: Click with pyautogui (like the 3/3 test)
print(f"\n3. pyautogui.click({sx},{sy})")
import pyautogui
pyautogui.FAILSAFE = False
pyautogui.click(sx, sy)
time.sleep(0.5)

actual2 = POINT()
u.GetCursorPos(ctypes.byref(actual2))
print(f"   Cursor after: ({actual2.x},{actual2.y})")

# Step 4: Also try clicking centre to see if ANYTHING registers
print(f"\n4. pyautogui.click({cx},{cy}) [centre test]")
pyautogui.click(cx, cy)
time.sleep(0.5)

actual3 = POINT()
u.GetCursorPos(ctypes.byref(actual3))
print(f"   Cursor after: ({actual3.x},{actual3.y})")

# Step 5: Try clicking with mouse_event directly
cx2, cy2 = cx, cy
print(f"\n5. Direct mouse_event at ({cx2},{cy2})")
u.SetCursorPos(cx2, cy2)
time.sleep(0.2)
u.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
time.sleep(0.1)
u.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
time.sleep(0.5)

print("\nDone! Did you see the cursor move? Did anything click?")
print("If cursor moved to LDPlayer but no click: input is blocked by LDPlayer")
print("If cursor didn't move: wrong coordinates") 

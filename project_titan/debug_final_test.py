"""Final live test: ADB touchscreen tap on PPPoker buttons at 720x1280."""
import subprocess, time, sys, numpy as np
from io import BytesIO
from PIL import Image

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEV = "emulator-5554"

def screenshot():
    r = subprocess.run([ADB, "-s", DEV, "shell", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return r.stdout.replace(b'\r\n', b'\n')

def to_img(data):
    return np.array(Image.open(BytesIO(data)))

def diff(a, b):
    ia, ib = to_img(a), to_img(b)
    if ia.shape != ib.shape: return -1.0
    return float(np.mean(np.abs(ia.astype(float) - ib.astype(float))))

def check_button(img, x, y, name):
    """Check if a button-colored pixel exists at (x,y)."""
    h, w = img.shape[:2]
    if y >= h or x >= w:
        return False, "OUT_OF_BOUNDS"
    r, g, b = int(img[y, x, 0]), int(img[y, x, 1]), int(img[y, x, 2])
    if name == "fold" and r > 100 and g < 80:
        return True, f"RED ({r},{g},{b})"
    if name == "call" and g > 100 and r < 80:
        return True, f"GREEN ({r},{g},{b})"
    if name == "raise" and r > 100 and g > 80 and b < 80:
        return True, f"YELLOW ({r},{g},{b})"
    return False, f"({r},{g},{b})"

# ---------- Preflight ----------
print("=== Preflight ===")
r = subprocess.run([ADB, "-s", DEV, "shell", "wm", "size"],
                   capture_output=True, text=True, timeout=5)
res = r.stdout.strip()
print(f"Resolution: {res}")
if "Override" in res:
    print("⚠ WARNING: wm size override detected! Resetting...")
    subprocess.run([ADB, "-s", DEV, "shell", "wm", "size", "reset"],
                   capture_output=True, timeout=5)
    time.sleep(1)

raw = screenshot()
img = to_img(raw)
print(f"Screenshot: {img.shape[1]}x{img.shape[0]}")

buttons = {
    "fold":  (126, 1220),
    "call":  (361, 1220),
    "raise": (596, 1220),
}

print("\nButton detection:")
any_visible = False
for name, (x, y) in buttons.items():
    visible, color = check_button(img, x, y, name)
    status = "✅ VISIBLE" if visible else "⬛ NOT VISIBLE"
    print(f"  {name:8s} ({x},{y}): {color} {status}")
    if visible:
        any_visible = True

if not any_visible:
    print("\n❌ No buttons visible. Please be at a table with action buttons.")
    print("   Run this script again when Fold/Call/Raise buttons are showing.")
    sys.exit(1)

# ---------- Click test ----------
print("\n=== Click Test (input touchscreen tap) ===")
# Pick the first visible button
for name, (x, y) in buttons.items():
    visible, _ = check_button(img, x, y, name)
    if visible:
        target_name = name
        target_x, target_y = x, y
        break

print(f"Target: {target_name} at ({target_x},{target_y})")

before = screenshot()
print(f"Sending: adb shell input touchscreen tap {target_x} {target_y}")
subprocess.run([ADB, "-s", DEV, "shell", "input", "touchscreen", "tap",
                str(target_x), str(target_y)],
               capture_output=True, timeout=10)
time.sleep(2.5)
after = screenshot()

d = diff(before, after)
print(f"Diff: {d:.2f}")

if d > 1.0:
    print(f"✅ CLICK WORKS! Screen changed significantly ({d:.2f}).")
    print(f"   The {target_name} button was pressed successfully!")
else:
    print(f"❌ No significant change ({d:.2f}). Click may not have worked.")

print("\nDone.")

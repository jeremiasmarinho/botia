"""Scan button positions at native 720x1280 and test all click methods."""
import subprocess, time, numpy as np
from io import BytesIO
from PIL import Image

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEV = "emulator-5554"

def shell(cmd: str) -> str:
    r = subprocess.run([ADB, "-s", DEV, "shell"] + cmd.split(),
                       capture_output=True, timeout=10, text=True)
    return r.stdout.strip()

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

# Verify resolution
print(f"Resolution: {shell('wm size')}")
print(f"Density: {shell('wm density')}")

# Take screenshot
raw = screenshot()
img = to_img(raw)
print(f"\nScreenshot shape: {img.shape}")  # Should be (1280, 720, 4) for 720x1280

# The old 1080x1920 coords were:
# fold (189, 1830), call (542, 1830), raise (894, 1830)
# Scale to 720x1280: x * 720/1080 = x * 0.667, y * 1280/1920 = y * 0.667
# fold (126, 1220), call (361, 1220), raise (596, 1220)

# But let's scan to find actual button positions
print("\n--- Scanning for colored buttons ---")
h, w = img.shape[:2]

# Scan bottom 25% of screen for brightly colored regions
bottom_start = int(h * 0.75)
print(f"Scanning y={bottom_start}..{h-1} for colored buttons")

# Look for red (fold), green (call), yellow (raise) pixels
for y in range(bottom_start, h, 3):
    row = img[y, :, :3]
    for x in range(0, w, 3):
        r, g, b = int(row[x, 0]), int(row[x, 1]), int(row[x, 2])
        # Red button (fold): high R, low G, low B
        if r > 120 and g < 80 and b < 80:
            print(f"  RED   (fold?) at ({x},{y}): RGB=({r},{g},{b})")
            break
    for x in range(0, w, 3):
        r, g, b = int(row[x, 0]), int(row[x, 1]), int(row[x, 2])
        # Green button (call): low R, high G, medium B
        if g > 120 and r < 80 and b < 100:
            print(f"  GREEN (call?) at ({x},{y}): RGB=({r},{g},{b})")
            break
    for x in range(0, w, 3):
        r, g, b = int(row[x, 0]), int(row[x, 1]), int(row[x, 2])
        # Yellow button (raise): high R, high G, low B
        if r > 120 and g > 100 and b < 80:
            print(f"  YELLOW(raise?) at ({x},{y}): RGB=({r},{g},{b})")
            break

# Also check the scaled positions
print("\n--- Check scaled positions ---")
scaled = {
    "fold":  (126, 1220),
    "call":  (361, 1220),
    "raise": (596, 1220),
}
for name, (x, y) in scaled.items():
    if y < h and x < w:
        px = img[y, x, :3]
        print(f"  {name:8s} ({x},{y}): RGB={px}")
    else:
        print(f"  {name:8s} ({x},{y}): OUT OF BOUNDS (img={w}x{h})")

# Check column at x=360 (roughly call button center)
print("\n--- Scan x=360, y=1100..1280 ---")
for y in range(1100, min(h, 1280), 5):
    if y < h:
        px = img[y, min(360, w-1), :3]
        print(f"  y={y}: RGB={px}")

# Save for visual inspection
Image.open(BytesIO(raw)).save(r"f:\botia\project_titan\reports\screen_720x1280.png")
print("\nSaved to reports/screen_720x1280.png")

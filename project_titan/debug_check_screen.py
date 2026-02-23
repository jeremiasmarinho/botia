"""Check what's at button positions + try center tap."""
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

# Take screenshot and inspect
print("Taking screenshot...")
raw = screenshot()
img = to_img(raw)
print(f"Screenshot shape: {img.shape}")  # (H, W, channels)

# Check pixels at button positions
positions = {
    "fold":  (189, 1830),
    "call":  (542, 1830),
    "raise": (894, 1830),
    "center": (540, 960),
}
print("\nPixel colors at button coordinates (x,y -> row=y, col=x):")
for name, (x, y) in positions.items():
    if y < img.shape[0] and x < img.shape[1]:
        px = img[y, x]
        print(f"  {name:8s} ({x:4d},{y:4d}): RGBA={px}")
    else:
        print(f"  {name:8s} ({x:4d},{y:4d}): OUT OF BOUNDS (img={img.shape[1]}x{img.shape[0]})")

# Also check a range around y=1830 to see if buttons shifted
print("\nScan y=1780..1900 at x=542 (call column):")
for y in range(1780, min(1901, img.shape[0]), 10):
    px = img[y, 542]
    print(f"  y={y}: RGBA={px}")

# Try tap at center with before/after
print("\n=== Tap center (540, 960) ===")
b = screenshot()
subprocess.run([ADB, "-s", DEV, "shell", "input", "mouse", "tap", "540", "960"],
               capture_output=True, timeout=10)
time.sleep(2.0)
a = screenshot()
d = diff(b, a)
print(f"  diff = {d:.2f}")

# Try with input tap (touchscreen) for comparison
print("\n=== Tap touchscreen center (540, 960) for comparison ===")
b2 = screenshot()
subprocess.run([ADB, "-s", DEV, "shell", "input", "touchscreen", "tap", "540", "960"],
               capture_output=True, timeout=10)
time.sleep(2.0)
a2 = screenshot()
d2 = diff(b2, a2)
print(f"  diff = {d2:.2f}")

# Try input mouse tap on fold with getevent monitoring
print("\n=== Checking if input mouse generates events ===")
# Start getevent in background
ge = subprocess.Popen(
    [ADB, "-s", DEV, "shell", "timeout", "3", "getevent", "-lt"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
time.sleep(0.5)
subprocess.run([ADB, "-s", DEV, "shell", "input", "mouse", "tap", "542", "1830"],
               capture_output=True, timeout=10)
time.sleep(3)
out, _ = ge.communicate(timeout=5)
events = out.decode(errors='replace')
print(f"  Events captured ({len(events)} chars):")
for line in events.strip().split('\n')[:30]:
    print(f"    {line}")

print("\nDone.")

"""Analyze current screenshot - check pixel regions to determine game state."""
import subprocess, numpy as np
from io import BytesIO
from PIL import Image

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEV = "emulator-5554"

r = subprocess.run([ADB, "-s", DEV, "shell", "screencap", "-p"],
                   capture_output=True, timeout=10)
raw = r.stdout.replace(b'\r\n', b'\n')
img = np.array(Image.open(BytesIO(raw)))
print(f"Image shape: {img.shape}")

# Check various regions
print("\n--- Button area (y=1780-1900) ---")
for y in range(1780, min(1901, img.shape[0]), 5):
    row = img[y, :, :3]  # RGB only
    avg = row.mean(axis=0)
    # Check specific x positions
    fold_px = img[y, 189, :3]
    call_px = img[y, 542, :3]
    raise_px = img[y, 894, :3]
    print(f"  y={y}: fold={fold_px} call={call_px} raise={raise_px} row_avg=({avg[0]:.0f},{avg[1]:.0f},{avg[2]:.0f})")

# Check center
print("\n--- Center area ---")
for y in range(400, 1600, 200):
    px = img[y, 540, :3]
    print(f"  y={y}, x=540: {px}")

# Check top (for lobby indicators)
print("\n--- Top area ---")
for y in range(0, 200, 20):
    px = img[y, 540, :3]
    print(f"  y={y}, x=540: {px}")

# Check if screen is all one color
print(f"\n--- Overall stats ---")
print(f"  min pixel: {img[:,:,:3].min()}")
print(f"  max pixel: {img[:,:,:3].max()}")
print(f"  mean: {img[:,:,:3].mean():.1f}")
print(f"  unique colors (sampled): {len(np.unique(img[::10,::10,:3].reshape(-1,3), axis=0))}")

# Save as viewable PNG
Image.open(BytesIO(raw)).save(r"f:\botia\project_titan\reports\current_screen_fixed.png")
print("\nSaved to reports/current_screen_fixed.png")

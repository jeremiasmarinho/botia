"""Compare the force_focus screenshots."""
import numpy as np
from PIL import Image

img1 = np.array(Image.open("reports/force_focus_after.png"))
img2 = np.array(Image.open("reports/force_focus_call.png"))

print(f"Image 1 (after centre click): {img1.shape}")
print(f"Image 2 (after call click): {img2.shape}")

diff = np.abs(img1[:,:,:3].astype(int) - img2[:,:,:3].astype(int))
print(f"\nOverall: mean_diff={diff.mean():.2f} max_diff={diff.max()}")

# Find regions with changes
print("\n--- Regions with significant changes ---")
h, w = img1.shape[:2]
for gi in range(8):
    for gj in range(6):
        y0, y1 = gi * h // 8, (gi + 1) * h // 8
        x0, x1 = gj * w // 6, (gj + 1) * w // 6
        region_diff = diff[y0:y1, x0:x1].mean()
        if region_diff > 2.0:
            print(f"  Grid ({gi},{gj}): y=[{y0}-{y1}] x=[{x0}-{x1}] mean_diff={region_diff:.2f}")

# Check button area specifically
print("\n--- Button area comparison (y=1780-1900) ---")
btn_diff = diff[1780:1900, :, :].mean()
print(f"  Overall button area diff: {btn_diff:.2f}")

for y in range(1780, 1900, 20):
    row_diff = diff[y, :, :].mean()
    if row_diff > 1.0:
        # Show which x regions changed
        for x_start in range(0, 1080, 60):
            region = diff[y:y+20, x_start:x_start+60, :].mean()
            if region > 5.0:
                p1 = img1[y+10, x_start+30, :3]
                p2 = img2[y+10, x_start+30, :3]
                print(f"  y={y}-{y+20} x={x_start}-{x_start+60}: "
                      f"before=({p1[0]},{p1[1]},{p1[2]}) after=({p2[0]},{p2[1]},{p2[2]}) "
                      f"diff={region:.1f}")

# Also check if PPPoker is showing a game state (look for common elements)
print("\n--- Screen state analysis ---")
# Check for dark background at top (game table)
top_mean = img2[100:200, 400:600, :3].mean()
print(f"  Top area (y=100-200, x=400-600) mean brightness: {top_mean:.0f}")

# Check for buttons at bottom
for y in range(1750, 1910, 10):
    row = img2[y, :, :3]
    # Green buttons
    green = ((row[:, 0] < 60) & (row[:, 1] > 100)).sum()
    # Red buttons  
    red = ((row[:, 0] > 120) & (row[:, 1] < 80) & (row[:, 2] < 80)).sum()
    # Dark (no buttons)
    dark = ((row[:, 0] < 30) & (row[:, 1] < 50) & (row[:, 2] < 50)).sum()
    # Table green
    table = ((row[:, 0] > 30) & (row[:, 0] < 55) & (row[:, 1] > 100) & (row[:, 1] < 180)).sum()
    if green > 100 or red > 50 or table > 500:
        print(f"  y={y}: green_btn={green:3d} red_btn={red:3d} dark={dark:3d} table_green={table:3d}")

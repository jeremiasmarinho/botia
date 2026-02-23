"""Compare before/after screenshots at button positions."""
import numpy as np
from PIL import Image

before = np.array(Image.open("reports/debug_before.png"))
after = np.array(Image.open("reports/debug_after.png"))

print(f"Before: {before.shape}")
print(f"After:  {after.shape}")

# Check key areas
areas = {
    "fold_btn": (1830, 189),
    "call_btn": (1830, 542),
    "raise_btn": (1830, 894),
    "centre_table": (960, 540),
    "centre_click_point": (530, 540),   # where focus click lands in android space
}

# Map centre click to android coords
# screen (1548,530) -> android: 
# ax = (1548-1268) * 1080 / 560 = 280 * 1080 / 560 = 540
# ay = (530-32) * 1920 / 997 = 498 * 1920 / 997 = 959
areas["focus_click_android"] = (959, 540)

for name, (y, x) in areas.items():
    if y < before.shape[0] and x < before.shape[1]:
        bp = before[y, x, :3]
        ap = after[y, x, :3]
        diff = np.abs(bp.astype(int) - ap.astype(int)).sum()
        print(f"  {name:25s}: before=({bp[0]:3d},{bp[1]:3d},{bp[2]:3d}) after=({ap[0]:3d},{ap[1]:3d},{ap[2]:3d}) diff={diff}")

# Check if buttons are still present in after
print("\n--- Button row in AFTER image (y=1830) ---")
for x in range(0, 1080, 40):
    p = after[1830, x, :3]
    print(f"  x={x:4d}: ({p[0]:3d},{p[1]:3d},{p[2]:3d})")

# Overall diff
diff_img = np.abs(before.astype(float) - after.astype(float))
diff_sum = diff_img.sum()
diff_mean = diff_img.mean()
print(f"\nOverall diff: sum={diff_sum:.0f}, mean={diff_mean:.2f}")

# Find regions with biggest changes
# Split into 4x4 grid
h, w = before.shape[:2]
for gi in range(4):
    for gj in range(4):
        y0, y1 = gi * h // 4, (gi + 1) * h // 4
        x0, x1 = gj * w // 4, (gj + 1) * w // 4
        region_diff = diff_img[y0:y1, x0:x1].mean()
        if region_diff > 1.0:
            print(f"  Grid ({gi},{gj}): y=[{y0}-{y1}] x=[{x0}-{x1}] mean_diff={region_diff:.2f}")

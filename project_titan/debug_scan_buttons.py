"""Scan the screenshot to find actual button positions."""
import numpy as np
from PIL import Image

img = np.array(Image.open("reports/debug_before.png"))
print(f"Image: {img.shape}")

# Scan bottom portion for button-like colored regions
# Red: R>120, G<80, B<80
# Green: R<60, G>100, B<120
# Yellow/Gold: R>120, G>100, B<80

print("\n--- Scanning bottom 400 rows for button colors ---")
for y in range(1500, 1920, 10):
    row = img[y, :, :3]
    # Count red, green, yellow pixels
    red_mask = (row[:, 0] > 120) & (row[:, 1] < 80) & (row[:, 2] < 80)
    green_mask = (row[:, 0] < 60) & (row[:, 1] > 100)
    yellow_mask = (row[:, 0] > 120) & (row[:, 1] > 100) & (row[:, 2] < 80)
    
    nr, ng, ny = red_mask.sum(), green_mask.sum(), yellow_mask.sum()
    if nr > 20 or ng > 20 or ny > 20:
        print(f"  y={y}: red={nr:3d}, green={ng:3d}, yellow={ny:3d}")

# Also scan for white text on colored backgrounds (button labels)
print("\n--- Scanning for white pixels (>230) in bottom area ---")
for y in range(1500, 1920, 10):
    row = img[y, :, :3]
    white_mask = (row[:, 0] > 230) & (row[:, 1] > 230) & (row[:, 2] > 230)
    nw = white_mask.sum()
    if nw > 10:
        # Find x ranges of white
        white_x = np.where(white_mask)[0]
        print(f"  y={y}: white_pixels={nw} x_range=[{white_x[0]}-{white_x[-1]}]")

# Show a few sample pixels across the whole width at key heights
print("\n--- Pixel samples across width ---")
for y in [1750, 1780, 1800, 1820, 1840, 1860, 1880, 1900]:
    pixels = []
    for x in range(0, 1080, 60):
        p = img[y, x, :3]
        pixels.append(f"({p[0]:3d},{p[1]:3d},{p[2]:3d})")
    print(f"  y={y}: {' '.join(pixels[:9])}")  # first 9 to keep readable
    print(f"         {' '.join(pixels[9:])}")

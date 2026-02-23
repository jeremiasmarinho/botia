"""Analyze screenshot to find PPPoker button positions."""
from PIL import Image

img = Image.open("reports/test_screenshot.png")
w, h = img.size
print(f"Screenshot size: {w} x {h}")

# Check expected button positions
for name, x, y in [("fold", 189, 1830), ("call", 542, 1830), ("raise", 894, 1830)]:
    if x < w and y < h:
        px = img.getpixel((x, y))
        print(f"  {name} ({x},{y}): pixel={px[:3]}")
    else:
        print(f"  {name} ({x},{y}): OUT OF BOUNDS")

# Scan bottom area to find buttons
print("\nBottom area pixel scan:")
for y in range(1750, min(1921, h), 15):
    samples = []
    for x in range(0, w, 80):
        px = img.getpixel((x, y))
        r, g, b = px[0], px[1], px[2]
        samples.append(f"{x}:({r},{g},{b})")
    print(f"  y={y}: {' | '.join(samples)}")

# Also scan middle/lower area to see if buttons are elsewhere
print("\nMid-lower area scan (y=1400-1750):")
for y in range(1400, 1750, 50):
    samples = []
    for x in range(0, w, 100):
        px = img.getpixel((x, y))
        r, g, b = px[0], px[1], px[2]
        samples.append(f"{x}:({r},{g},{b})")
    print(f"  y={y}: {' | '.join(samples)}")

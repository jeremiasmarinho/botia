"""Test TitanOCR with enhanced yellow text isolation on debug frames."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2
import numpy as np

# Load a MuMu frame
frames = []
for name in ["screenA.png", "screenB.png", "screenC.png", "screenD.png", "screenE.png", "screenF.png"]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", name)
    if os.path.exists(path):
        img = cv2.imread(path)
        if img is not None:
            # Resize to 720x1280
            img = cv2.resize(img, (720, 1280))
            frames.append((name, img))

if not frames:
    print("No frames found!")
    sys.exit(1)

# OCR regions from updated config
regions = {
    "pot":   (310, 130, 130, 70),
    "stack": (410, 1022, 175, 40),
    "call":  (300, 1210, 125, 30),
}

from agent.vision_ocr import TitanOCR
ocr = TitanOCR()

print(f"Found {len(frames)} frames\n")

for fname, frame in frames:
    print(f"=== {fname} ===")
    for region_name, (x, y, w, h) in regions.items():
        crop = frame[y:y+h, x:x+w]
        
        # Check what colours we see
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        yellow_mask = cv2.inRange(hsv, (15, 60, 100), (40, 255, 255))
        white_mask = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
        n_yellow = int(np.sum(yellow_mask > 0))
        n_white = int(np.sum(white_mask > 0))
        total = crop.shape[0] * crop.shape[1]
        
        value = ocr.read_numeric_region(crop, key=region_name, fallback=0.0)
        print(f"  {region_name:6s}: value={value:>10.1f}  "
              f"yellow={n_yellow:4d}/{total} ({100*n_yellow/total:.1f}%)  "
              f"white={n_white:4d}/{total} ({100*n_white/total:.1f}%)")
    print()

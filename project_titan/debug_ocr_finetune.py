"""Fine-tune OCR regions — test various y offsets to find the optimal stack position."""
import cv2
import numpy as np
import os

# Capture fresh screenshot
from agent.vision_yolo import VisionYolo
vision = VisionYolo()
if not vision.find_window():
    print("LDPlayer not found!")
    exit(1)

frame = vision.capture_frame()
if frame is None:
    print("Capture failed!")
    exit(1)

h, w = frame.shape[:2]
print(f"Frame: {w}x{h}")
cv2.imwrite("reports/screenshot_fresh.png", frame)

ref_w, ref_h = 720, 1280
sx = w / ref_w
sy = h / ref_h

try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except ImportError:
    print("pytesseract not installed!")
    exit(1)

# Test stack region at various android Y positions
print()
print("=" * 60)
print("STACK REGION SWEEP (x=210, w=140, h=38)")
print("=" * 60)
for android_y in range(1125, 1175, 3):
    cx = int(210 * sx)
    cy = int(android_y * sy)
    cw = int(140 * sx)
    ch = int(38 * sy)
    
    if cy + ch > h or cy < 0:
        continue
    
    crop = frame[cy:cy+ch, cx:cx+cw]
    if crop.size == 0:
        continue
    
    # Preprocess
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    upscaled = cv2.resize(gray, (cw * 2, ch * 2), interpolation=cv2.INTER_CUBIC)
    blurred = cv2.GaussianBlur(upscaled, (3, 3), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    text = pytesseract.image_to_string(thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,").strip()
    
    # Also try raw OCR
    raw = pytesseract.image_to_string(gray, config="--psm 7").strip()
    
    marker = "  ✅" if text and text.replace(".", "").replace(",", "").isdigit() else ""
    if text or raw:
        print(f"  android_y={android_y:4d} → canvas_y={cy:4d}: digits='{text}' raw='{raw}'{marker}")

# Also sweep wider x for stack
print()
print("=" * 60)
print("STACK X-SWEEP at android_y=1145 (h=38)")
print("=" * 60)
for android_x in range(150, 350, 20):
    for android_w in [100, 140, 180]:
        cx = int(android_x * sx)
        cy = int(1145 * sy)
        cw = int(android_w * sx)
        ch = int(38 * sy)
        
        if cx + cw > w or cy + ch > h:
            continue
        
        crop = frame[cy:cy+ch, cx:cx+cw]
        if crop.size == 0:
            continue
        
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, (cw * 2, ch * 2), interpolation=cv2.INTER_CUBIC)
        blurred = cv2.GaussianBlur(upscaled, (3, 3), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        text = pytesseract.image_to_string(thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,").strip()
        raw = pytesseract.image_to_string(gray, config="--psm 7").strip()
        
        if text or raw:
            marker = "  ✅" if text and text.replace(".", "").replace(",", "").isdigit() else ""
            print(f"  x={android_x:3d} w={android_w:3d} → canvas({cx},{cy},{cw},{ch}): digits='{text}' raw='{raw}'{marker}")

# Test call region (button area)
print()
print("=" * 60)
print("CALL REGION SWEEP (button area)")
print("=" * 60)
for android_y in range(1195, 1240, 3):
    for section, ax, aw in [("center", 250, 200), ("right", 450, 200), ("centright", 340, 260)]:
        cx = int(ax * sx)
        cy = int(android_y * sy)
        cw = int(aw * sx)
        ch = int(45 * sy)
        
        if cx + cw > w or cy + ch > h:
            continue
        
        crop = frame[cy:cy+ch, cx:cx+cw]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        
        raw = pytesseract.image_to_string(gray, config="--psm 7").strip()
        digits = pytesseract.image_to_string(gray, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,").strip()
        
        if raw or digits:
            print(f"  {section:>9s} y={android_y:4d} → canvas({cx},{cy},{cw},{ch}): digits='{digits}' raw='{raw}'")

# Save key crops
print()
print("Saving key region crops for inspection...")
os.makedirs("reports", exist_ok=True)

# Save hero bottom area with grid
bottom = frame[int(h*0.83):, :].copy()
bh, bw = bottom.shape[:2]
# Draw horizontal guides every 20px
for gy in range(0, bh, 20):
    actual_cy = int(h*0.83) + gy
    android_y = int(actual_cy / sy)
    cv2.line(bottom, (0, gy), (bw, gy), (0, 255, 255), 1)
    cv2.putText(bottom, f"ay={android_y}", (5, gy+12), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 255, 255), 1)
cv2.imwrite("reports/ocr_hero_grid.png", bottom)
print("  reports/ocr_hero_grid.png — hero area with android Y grid")

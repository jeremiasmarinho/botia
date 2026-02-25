"""Check stack text position across all frames."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cv2, numpy as np
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

frame_files = [f"screen{c}.png" for c in "ABCDEF"]
frames = []
for fname in frame_files:
    path = os.path.join(os.path.dirname(__file__), "..", "..", fname)
    if os.path.exists(path):
        img = cv2.imread(path)
        if img is not None:
            frames.append((fname, cv2.resize(img, (720, 1280))))

# Test tight regions
tight_regions = {
    "pot_tight":   (330, 150, 80, 35),
    "stack_v1":    (430, 1028, 160, 30),
    "stack_v2":    (420, 1025, 175, 32),
    "stack_v3":    (410, 1020, 190, 40),
    "call_btn":    (290, 1195, 150, 40),
    "call_btn2":   (300, 1200, 130, 35),
}

for fname, frame in frames:
    print(f"\n=== {fname} ===")
    for name, (x, y, w, h) in tight_regions.items():
        crop = frame[y:y+h, x:x+w]
        
        # Try yellow-only for pot, white-strict for stack, combined for call
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        
        yellow = cv2.inRange(hsv, (10, 80, 80), (35, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 220), (180, 30, 255))
        combined = cv2.bitwise_or(yellow, white)
        
        ch, cw = crop.shape[:2]
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        
        results = {}
        for mask_name, mask in [("yellow", yellow), ("white", white), ("combined", combined)]:
            up = cv2.resize(mask, (cw*6, ch*6), interpolation=cv2.INTER_NEAREST)
            up = cv2.dilate(up, kernel, iterations=1)
            n_px = int(np.sum(mask > 0))
            
            for psm in [7]:
                config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
                text = pytesseract.image_to_string(up, config=config).strip()
                if text:
                    results[f"{mask_name}"] = f"{text} ({n_px}px)"
        
        if results:
            parts = [f"{k}={v}" for k, v in results.items()]
            print(f"  {name:12s}: {', '.join(parts)}")
        else:
            print(f"  {name:12s}: (no text)")

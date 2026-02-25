"""Analyze actual text pixels in OCR regions to find optimal boundaries."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2
import numpy as np
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

path = os.path.join(os.path.dirname(__file__), "..", "..", "screenA.png")
frame = cv2.imread(path)
frame = cv2.resize(frame, (720, 1280))

os.makedirs("reports/ocr_debug2", exist_ok=True)

# Test various tight regions
test_regions = {
    # Pot - try various positions near y=130-200
    "pot_tight":      (310, 140, 120, 50),
    "pot_narrow":     (320, 155, 100, 35),
    "pot_wider":      (280, 120, 160, 80),
    
    # Stack - tighter around text
    "stack_tight":    (410, 1022, 190, 35),
    "stack_narrow":   (420, 1025, 170, 28),
    "stack_current":  (405, 1015, 200, 50),
    
    # Call button text - around the call button (361, 1220)
    "call_button":    (285, 1190, 160, 40),
    "call_btn_wider": (270, 1185, 190, 50),
    "call_btn_above": (285, 1100, 160, 40),
    "call_btn_row":   (100, 1190, 500, 45),
}

for name, (x, y, w, h) in test_regions.items():
    crop = frame[y:y+h, x:x+w]
    cv2.imwrite(f"reports/ocr_debug2/{name}_crop.png", crop)
    
    # Yellow only isolation
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (15, 80, 100), (40, 255, 255))
    n_yellow = int(np.sum(yellow > 0))
    
    # Strict white: V>200, S<40
    white_strict = cv2.inRange(hsv, (0, 0, 200), (180, 40, 255))
    n_white_s = int(np.sum(white_strict > 0))
    
    # Loose white: V>170, S<60
    white_loose = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
    n_white_l = int(np.sum(white_loose > 0))
    
    total = w * h
    
    # Try OCR with yellow only (upscaled)
    ch, cw = yellow.shape
    yellow_up = cv2.resize(yellow, (cw * 4, ch * 4), interpolation=cv2.INTER_NEAREST)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    yellow_up = cv2.dilate(yellow_up, kernel, iterations=1)
    
    # Try OCR with strict white only
    white_s_up = cv2.resize(white_strict, (cw * 4, ch * 4), interpolation=cv2.INTER_NEAREST)
    white_s_up = cv2.dilate(white_s_up, kernel, iterations=1)
    
    # Combined yellow + strict white
    combined = cv2.bitwise_or(yellow, white_strict)
    combined_up = cv2.resize(combined, (cw * 4, ch * 4), interpolation=cv2.INTER_NEAREST)
    combined_up = cv2.dilate(combined_up, kernel, iterations=1)
    
    cv2.imwrite(f"reports/ocr_debug2/{name}_yellow.png", yellow_up)
    cv2.imwrite(f"reports/ocr_debug2/{name}_white_strict.png", white_s_up)
    cv2.imwrite(f"reports/ocr_debug2/{name}_combined.png", combined_up)
    
    results = {}
    for img_name, img in [("yellow", yellow_up), ("white_strict", white_s_up), ("combined", combined_up)]:
        for psm in [7, 6]:
            config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
            text = pytesseract.image_to_string(img, config=config).strip()
            if text:
                results[f"{img_name}_psm{psm}"] = text
    
    print(f"{name:18s}  y={n_yellow:4d} ws={n_white_s:4d} wl={n_white_l:4d} tot={total:5d}  "
          f"| {', '.join(f'{k}={v}' for k, v in results.items())}")

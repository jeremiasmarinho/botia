"""Deep debug of TitanOCR preprocessing and raw OCR output."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Load one frame
path = os.path.join(os.path.dirname(__file__), "..", "..", "screenA.png")
frame = cv2.imread(path)
frame = cv2.resize(frame, (720, 1280))

regions = {
    "pot":   (300, 130, 140, 70),
    "stack": (405, 1015, 200, 50),
    "call":  (100, 1095, 550, 50),
}

os.makedirs("reports/ocr_debug", exist_ok=True)

for name, (x, y, w, h) in regions.items():
    crop = frame[y:y+h, x:x+w]
    cv2.imwrite(f"reports/ocr_debug/{name}_crop.png", crop)
    
    # Strategy 0: Yellow+white colour isolation
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow_mask = cv2.inRange(hsv, (15, 60, 100), (40, 255, 255))
    white_mask = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
    colour_mask = cv2.bitwise_or(yellow_mask, white_mask)
    
    ch, cw = colour_mask.shape[:2]
    scale = 4
    colour_up = cv2.resize(colour_mask, (cw * scale, ch * scale), interpolation=cv2.INTER_NEAREST)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    colour_up = cv2.dilate(colour_up, kernel, iterations=1)
    cv2.imwrite(f"reports/ocr_debug/{name}_colour.png", colour_up)
    
    # Check text density
    text_px = float(np.mean(colour_up < 128))
    print(f"\n=== {name} ===")
    print(f"  Crop shape: {crop.shape}, colour mask shape: {colour_up.shape}")
    print(f"  Text density (dark px): {text_px:.3f}")
    
    # Also try yellow-only mask
    yellow_up = cv2.resize(yellow_mask, (cw * scale, ch * scale), interpolation=cv2.INTER_NEAREST)
    yellow_up = cv2.dilate(yellow_up, kernel, iterations=1)
    cv2.imwrite(f"reports/ocr_debug/{name}_yellow.png", yellow_up)
    
    # White only mask
    white_up = cv2.resize(white_mask, (cw * scale, ch * scale), interpolation=cv2.INTER_NEAREST)
    white_up = cv2.dilate(white_up, kernel, iterations=1)
    cv2.imwrite(f"reports/ocr_debug/{name}_white.png", white_up)
    
    # Run tesseract on each
    for img_name, img in [("colour", colour_up), ("yellow", yellow_up), ("white", white_up)]:
        for psm in [7, 6, 13]:
            config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
            try:
                text = pytesseract.image_to_string(img, config=config).strip()
            except Exception as e:
                text = f"ERR: {e}"
            if text:
                print(f"  {img_name} psm{psm}: '{text}'")
    
    # Also try grayscale approaches
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    upscaled = cv2.resize(gray, (cw * 3, ch * 3), interpolation=cv2.INTER_CUBIC)
    blurred = cv2.GaussianBlur(upscaled, (3, 3), 0)
    
    # CLAHE + OTSU
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(blurred)
    _, thresh1 = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cv2.imwrite(f"reports/ocr_debug/{name}_clahe_otsu.png", thresh1)
    
    # Fixed threshold
    _, thresh3 = cv2.threshold(blurred, 140, 255, cv2.THRESH_BINARY)
    cv2.imwrite(f"reports/ocr_debug/{name}_fixed140.png", thresh3)
    
    for img_name, img in [("clahe_otsu", thresh1), ("fixed140", thresh3)]:
        # Try both normal and inverted
        for polarity, pimg in [("normal", img), ("inverted", cv2.bitwise_not(img))]:
            for psm in [7, 6]:
                config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
                try:
                    text = pytesseract.image_to_string(pimg, config=config).strip()
                except Exception as e:
                    text = f"ERR: {e}"
                if text:
                    print(f"  {img_name}_{polarity} psm{psm}: '{text}'")

print("\n\nDebug images saved to reports/ocr_debug/")

"""Fine-tune OCR regions: scan sub-areas for best text detection."""
import cv2
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

frame = cv2.imread('reports/debug_cards/1771925962254_cards.png')
print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")

# Refined test regions based on text scan
regions = {
    # Pot area — scan vertically around center
    'pot_y120':   (300, 120, 150, 40),
    'pot_y140':   (300, 140, 150, 40),
    'pot_y160':   (300, 160, 150, 40),
    'pot_y180':   (300, 180, 150, 40),
    'pot_y200':   (290, 200, 160, 40),
    'pot_y220':   (280, 220, 170, 40),
    'pot_y240':   (280, 240, 160, 40),
    'pot_y400':   (280, 400, 120, 40),
    'pot_y420':   (280, 420, 120, 40),
    'pot_y440':   (300, 440, 100, 30),
    # Stack area — various x/y
    'stk_405_1020': (405, 1020, 200, 40),
    'stk_405_1040': (405, 1040, 200, 40),
    'stk_405_1060': (405, 1060, 200, 40),
    'stk_405_1080': (405, 1080, 200, 40),
    'stk_405_1100': (405, 1100, 200, 40),
    'stk_405_1120': (405, 1120, 200, 40),
    # Call button — the call button text
    'call_300_1210': (300, 1210, 140, 35),
    'call_300_1220': (300, 1220, 140, 35),
    'call_330_1220': (330, 1220, 100, 30),
    'call_350_1225': (350, 1225, 80, 25),
}

for name, (rx, ry, rw, rh) in regions.items():
    if ry + rh > frame.shape[0] or rx + rw > frame.shape[1]:
        continue
    crop = frame[ry:ry+rh, rx:rx+rw]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    up = cv2.resize(gray, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    # Try OTSU
    _, bw = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cfg = '--psm 7 -c tessedit_char_whitelist=0123456789.$,KkMm'
    try:
        t1 = pytesseract.image_to_string(bw, config=cfg, timeout=3).strip()
    except Exception:
        t1 = ""
    try:
        t2 = pytesseract.image_to_string(255-bw, config=cfg, timeout=3).strip()
    except Exception:
        t2 = ""

    # Also try yellow text isolation
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (15, 80, 100), (40, 255, 255))
    white_text = cv2.inRange(hsv, (0, 0, 180), (180, 50, 255))
    combined = cv2.bitwise_or(yellow, white_text)
    combined_up = cv2.resize(combined, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    try:
        t3 = pytesseract.image_to_string(combined_up, config=cfg, timeout=3).strip()
    except Exception:
        t3 = ""

    if t1 or t2 or t3:
        print(f'{name:20s}: ocr="{t1}", inv="{t2}", yellow="{t3}"')
    else:
        mean_g = gray.mean()
        if mean_g > 120:  # potentially interesting region
            print(f'{name:20s}: (bright gray={mean_g:.0f} but no text)')

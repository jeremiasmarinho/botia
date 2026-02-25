"""Debug script: test OCR at various regions to find pot/stack/call text."""
import cv2
import numpy as np
import pytesseract
import os

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

frame = cv2.imread('reports/debug_cards/1771925962254_cards.png')
if frame is None:
    print("Frame not found")
    exit()

print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")

# Test various regions where text was found
test_regions = {
    'pot_current':  (240, 385, 260, 55),
    'pot_higher':   (280, 100, 200, 70),
    'pot_mid':      (290, 200, 170, 60),
    'pot_area':     (280, 390, 120, 80),
    'stack_current':(270, 1080, 155, 60),
    'stack_right':  (405, 1020, 200, 60),
    'stack_right2': (405, 1060, 200, 80),
    'call_current': (470, 1210, 155, 35),
    'call_wide':    (100, 1200, 560, 45),
    'buttons_area': (100, 1220, 560, 30),
}

for name, (rx, ry, rw, rh) in test_regions.items():
    if ry + rh > frame.shape[0] or rx + rw > frame.shape[1]:
        continue
    crop = frame[ry:ry+rh, rx:rx+rw]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Simple OCR
    upscaled = cv2.resize(gray, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    config = '--psm 7 -c tessedit_char_whitelist=0123456789.$,KkMm'
    try:
        text = pytesseract.image_to_string(binary, config=config, timeout=3).strip()
    except Exception:
        text = "ERR"
    try:
        text_inv = pytesseract.image_to_string(255 - binary, config=config, timeout=3).strip()
    except Exception:
        text_inv = "ERR"

    mean_g = gray.mean()
    print(f'{name:20s} ({rx},{ry},{rw}x{rh}): gray={mean_g:.0f}, OCR="{text}", inv="{text_inv}"')
    cv2.imwrite(f'reports/debug_ocr_{name}.png', crop)

print("\nSaved OCR crops to reports/")

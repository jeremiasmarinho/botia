"""OCR at corrected text cluster positions."""
import cv2
import numpy as np
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

frame = cv2.imread('reports/debug_cards/1771925962254_cards.png')

def ocr_color(crop, scale=4):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (15, 60, 100), (40, 255, 255))
    white = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
    mask = cv2.bitwise_or(yellow, white)
    up = cv2.resize(mask, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    up = cv2.dilate(up, k, iterations=1)
    cfg7 = '--psm 7 -c tessedit_char_whitelist=0123456789.$,KkMm '
    cfg6 = '--psm 6 -c tessedit_char_whitelist=0123456789.$,KkMm '
    try:
        t7 = pytesseract.image_to_string(up, config=cfg7, timeout=3).strip()
    except Exception:
        t7 = ''
    try:
        t6 = pytesseract.image_to_string(up, config=cfg6, timeout=3).strip()
    except Exception:
        t6 = ''
    return t7, t6

print("=== POT ===")
for y1, y2, x1, x2 in [
    (118, 195, 310, 440),
    (130, 170, 320, 420),
    (140, 180, 320, 430),
    (160, 200, 320, 420),
]:
    crop = frame[y1:y2, x1:x2]
    t7, t6 = ocr_color(crop)
    cv2.imwrite(f'reports/debug_ocr_pot_{y1}.png', crop)
    print(f'  ({x1},{y1},{x2-x1}x{y2-y1}): psm7="{t7}", psm6="{t6}"')

print("\n=== STACK ===")
for y1, y2, x1, x2 in [
    (1018, 1099, 405, 602),
    (1020, 1060, 410, 600),
    (1040, 1080, 410, 600),
    (1060, 1100, 410, 600),
]:
    crop = frame[y1:y2, x1:x2]
    t7, t6 = ocr_color(crop)
    print(f'  ({x1},{y1},{x2-x1}x{y2-y1}): psm7="{t7}", psm6="{t6}"')

print("\n=== BUTTONS ===")
for y1, y2, x1, x2 in [
    (1100, 1140, 100, 650),
    (1195, 1240, 100, 650),
    (1200, 1250, 260, 470),
]:
    crop = frame[y1:y2, x1:x2]
    t7, t6 = ocr_color(crop, scale=5)
    print(f'  ({x1},{y1},{x2-x1}x{y2-y1}): psm7="{t7}", psm6="{t6}"')

# Also save the PPPoker-yellow-isolated mask for the whole pot area
pot = frame[100:210, 290:460]
hsv = cv2.cvtColor(pot, cv2.COLOR_BGR2HSV)
y = cv2.inRange(hsv, (15, 60, 100), (40, 255, 255))
w = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
mask = cv2.bitwise_or(y, w)
cv2.imwrite('reports/debug_ocr_pot_mask.png', mask)
cv2.imwrite('reports/debug_ocr_pot_raw.png', pot)
print("\nSaved pot mask and raw to reports/")

"""Find pot/stack/call using color-targeted text extraction."""
import cv2
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

frame = cv2.imread('reports/debug_cards/1771925962254_cards.png')
print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")

def extract_text_yellow(crop, scale=4):
    """Extract yellow/white text from PPPoker background using HSV isolation."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Yellow text (PPPoker gold): H=15-40, S>60, V>100
    yellow = cv2.inRange(hsv, (15, 60, 100), (40, 255, 255))
    # White text: low S, high V
    white = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
    # Combine and upscale
    mask = cv2.bitwise_or(yellow, white)
    mask_up = cv2.resize(mask, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    # Dilate slightly to connect broken chars
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask_up = cv2.dilate(mask_up, kernel, iterations=1)

    cfg = '--psm 7 -c tessedit_char_whitelist=0123456789.$,KkMmPpOoTt '
    try:
        text = pytesseract.image_to_string(mask_up, config=cfg, timeout=3).strip()
    except Exception:
        text = ""
    return text, mask

# === POT REGION SCAN ===
print("\n=== POT SCAN (center, y=100-500) ===")
for y in range(80, 500, 20):
    for x_start in [240, 280, 320]:
        rx, ry, rw, rh = x_start, y, 200, 35
        if ry + rh > frame.shape[0] or rx + rw > frame.shape[1]:
            continue
        crop = frame[ry:ry+rh, rx:rx+rw]
        text, mask = extract_text_yellow(crop)
        text = text.strip()
        n_text_px = np.sum(mask > 0)
        if text and len(text) >= 2:
            print(f"  ({rx},{ry},{rw}x{rh}): \"{text}\" (text_px={n_text_px})")

# === STACK SCAN ===
print("\n=== STACK SCAN (right side, y=1000-1160) ===")
for y in range(1000, 1160, 15):
    for x_start in [370, 400, 430]:
        rx, ry, rw, rh = x_start, y, 220, 30
        if ry + rh > frame.shape[0] or rx + rw > frame.shape[1]:
            continue
        crop = frame[ry:ry+rh, rx:rx+rw]
        text, mask = extract_text_yellow(crop)
        text = text.strip()
        n_text_px = np.sum(mask > 0)
        if text and len(text) >= 2:
            print(f"  ({rx},{ry},{rw}x{rh}): \"{text}\" (text_px={n_text_px})")

# === CALL BUTTON SCAN ===
print("\n=== CALL BUTTON SCAN (y=1195-1250) ===")
for y in range(1195, 1250, 10):
    for x_start in [280, 310, 340]:
        rx, ry, rw, rh = x_start, y, 130, 25
        if ry + rh > frame.shape[0] or rx + rw > frame.shape[1]:
            continue
        crop = frame[ry:ry+rh, rx:rx+rw]
        text, mask = extract_text_yellow(crop)
        text = text.strip()
        if text:
            print(f"  ({rx},{ry},{rw}x{rh}): \"{text}\"")

# === ALSO CHECK FOLD/RAISE BUTTONS ===
print("\n=== FOLD/RAISE BUTTONS ===")
for name, (bx, by) in [('fold', (126, 1220)), ('call', (361, 1220)), ('raise', (596, 1220))]:
    rx, ry = bx - 60, by - 15
    rw, rh = 120, 30
    if ry + rh > frame.shape[0] or rx + rw > frame.shape[1]:
        continue
    crop = frame[ry:ry+rh, rx:rx+rw]
    text, mask = extract_text_yellow(crop)
    print(f"  {name} ({rx},{ry},{rw}x{rh}): \"{text}\"")

# Save the most promising crops
for name, (rx, ry, rw, rh) in [
    ('pot_best', (280, 200, 200, 50)),
    ('stk_best', (400, 1020, 210, 40)),
    ('call_btn', (300, 1210, 130, 35)),
]:
    crop = frame[ry:ry+rh, rx:rx+rw]
    cv2.imwrite(f'reports/debug_ocr_{name}.png', crop)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (15, 60, 100), (40, 255, 255))
    white = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
    mask = cv2.bitwise_or(yellow, white)
    cv2.imwrite(f'reports/debug_ocr_{name}_mask.png', mask)

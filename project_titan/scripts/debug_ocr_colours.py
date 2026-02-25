"""Examine pixel colors in pot/stack/call regions to calibrate thresholds."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cv2, numpy as np

path = os.path.join(os.path.dirname(__file__), "..", "..", "screenA.png")
frame = cv2.imread(path)
frame = cv2.resize(frame, (720, 1280))

# Scan for ANY text-like pixels in the pot area (wider search)
pot_search = frame[80:250, 240:480]  # Big search area for pot
hsv = cv2.cvtColor(pot_search, cv2.COLOR_BGR2HSV)

print("=== POT AREA COLOUR ANALYSIS (y=80-250, x=240-480) ===")
# Find all coloured pixels (not dark background)
for label, lo, hi in [
    ("Yellow (H15-40,S60+,V100+)", (15,60,100), (40,255,255)),
    ("Gold (H10-25,S100+,V150+)", (10,100,150), (25,255,255)),
    ("Orange (H5-20,S80+,V100+)", (5,80,100), (20,255,255)),
    ("Bright any (S30+,V150+)", (0,30,150), (180,255,255)),
    ("White (S<40,V200+)", (0,0,200), (180,40,255)),
    ("Very bright (V220+)", (0,0,220), (180,255,255)),
]:
    mask = cv2.inRange(hsv, lo, hi)
    n = int(np.sum(mask > 0))
    if n > 0:
        ys, xs = np.where(mask > 0)
        # Sample some pixel BGR values
        bgr_samples = pot_search[ys[:5], xs[:5]]
        hsv_samples = hsv[ys[:5], xs[:5]]
        y_range = (int(ys.min()) + 80, int(ys.max()) + 80)
        x_range = (int(xs.min()) + 240, int(xs.max()) + 240)
        print(f"  {label}: {n:5d} px  y={y_range} x={x_range}")
        for i in range(min(3, len(bgr_samples))):
            b, g, r = bgr_samples[i]
            h, s, v = hsv_samples[i]
            print(f"    sample: BGR=({b},{g},{r}) HSV=({h},{s},{v})")

# Now look at specific pot text coordinates
print("\n=== PRECISE POT PIXELS (y=155-175, x=330-420) ===")
pot_precise = frame[155:175, 330:420]
hsv_p = cv2.cvtColor(pot_precise, cv2.COLOR_BGR2HSV)
gray_p = cv2.cvtColor(pot_precise, cv2.COLOR_BGR2GRAY)
print(f"Mean gray: {np.mean(gray_p):.1f}")
print(f"Max gray: {np.max(gray_p)}")
print(f"Min gray: {np.min(gray_p)}")
# Find bright pixels
bright = gray_p > 100
n_bright = int(np.sum(bright))
total_p = pot_precise.shape[0] * pot_precise.shape[1]
print(f"Bright pixels (>100): {n_bright}/{total_p} ({100*n_bright/total_p:.1f}%)")

if n_bright > 0:
    ys, xs = np.where(bright)
    for i in range(min(5, len(ys))):
        b, g, r = pot_precise[ys[i], xs[i]]
        h, s, v = hsv_p[ys[i], xs[i]]
        print(f"  bright pixel ({xs[i]+330},{ys[i]+155}): BGR=({b},{g},{r}) HSV=({h},{s},{v})")

# Show where text might be more broadly
print("\n=== SCANNING FOR TEXT IN ROWS (x=300-420) ===")
for y_start in range(80, 260, 10):
    row = frame[y_start:y_start+10, 300:420]
    gray_r = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
    max_val = int(np.max(gray_r))
    mean_val = float(np.mean(gray_r))
    n_bright = int(np.sum(gray_r > 120))
    if n_bright > 0 or max_val > 120:
        hsv_r = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
        ys, xs = np.where(gray_r > 120)
        if len(ys) > 0:
            b, g, r = row[ys[0], xs[0]]
            h, s, v = hsv_r[ys[0], xs[0]]
            print(f"  y={y_start:3d}-{y_start+10}: max={max_val} mean={mean_val:.0f} "
                  f"bright={n_bright:3d}  sample BGR=({b},{g},{r}) HSV=({h},{s},{v})")

# Check stack text precisely
print("\n=== STACK TEXT COLOURS (y=1025-1055, x=410-600) ===")
stack_crop = frame[1025:1055, 410:600]
hsv_s = cv2.cvtColor(stack_crop, cv2.COLOR_BGR2HSV)
gray_s = cv2.cvtColor(stack_crop, cv2.COLOR_BGR2GRAY)
# Find the actual text pixels
for thresh in [180, 200, 220]:
    mask = gray_s > thresh
    n = int(np.sum(mask))
    if n > 0:
        ys, xs = np.where(mask)
        samples = stack_crop[ys[:3], xs[:3]]
        h_samples = hsv_s[ys[:3], xs[:3]]
        print(f"  gray>{thresh}: {n:4d} px  "
              f"sample BGR=({samples[0][0]},{samples[0][1]},{samples[0][2]}) "
              f"HSV=({h_samples[0][0]},{h_samples[0][1]},{h_samples[0][2]})")

# Save zoomed pot crop for visual inspection
pot_crop = frame[100:220, 280:460]
cv2.imwrite("reports/ocr_debug2/pot_full_search.png", pot_crop)
cv2.imwrite("reports/ocr_debug2/pot_precise.png", pot_precise)
cv2.imwrite("reports/ocr_debug2/stack_precise.png", stack_crop)

# Also try OCR on the precise pot crop with high upscale
pot_up = cv2.resize(pot_precise, (pot_precise.shape[1]*8, pot_precise.shape[0]*8), interpolation=cv2.INTER_CUBIC)
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
for psm in [7, 6, 13]:
    config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
    text = pytesseract.image_to_string(pot_up, config=config).strip()
    if text: print(f"  pot_precise 8x gray psm{psm}: '{text}'")

# Try inverted
pot_up_inv = cv2.bitwise_not(pot_up)
for psm in [7, 6]:
    config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
    text = pytesseract.image_to_string(pot_up_inv, config=config).strip()
    if text: print(f"  pot_precise 8x inv psm{psm}: '{text}'")

# Try OTSU on pot
_, pot_otsu = cv2.threshold(pot_up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
for psm in [7, 6]:
    config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
    text = pytesseract.image_to_string(pot_otsu, config=config).strip()
    if text: print(f"  pot_precise 8x otsu psm{psm}: '{text}'")
    text = pytesseract.image_to_string(cv2.bitwise_not(pot_otsu), config=config).strip()
    if text: print(f"  pot_precise 8x otsu_inv psm{psm}: '{text}'")

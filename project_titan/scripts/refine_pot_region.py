"""Refine pot region position on ADB screenshot."""
import subprocess
import numpy as np
import cv2
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
adb = r"F:\LDPlayer\LDPlayer9\adb.exe"

r = subprocess.run([adb, "-s", "emulator-5554", "exec-out", "screencap", "-p"], capture_output=True)
buf = np.frombuffer(r.stdout, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
print(f"Frame: {img.shape[1]}x{img.shape[0]}")

# Focus on y=420-510 area where "Pot3" was found 
# Try different thresholds and methods
print("\n=== Detailed Y scan around pot area (y=400-520) ===")
for y_start in range(400, 520, 10):
    for x_start in [100, 150, 200, 250, 300]:
        for width in [200, 280, 360]:
            crop = img[y_start:y_start+40, x_start:x_start+width]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            
            # Try multiple thresholds
            for thresh_val in [150, 180, 200]:
                _, thresh = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)
                white_pct = np.mean(thresh > 0) * 100
                if white_pct < 2:
                    continue
                text = pytesseract.image_to_string(
                    thresh, config="--psm 7"
                ).strip()
                if text and any(c.isdigit() for c in text):
                    print(f"  ({x_start},{y_start},{width},40) t={thresh_val}: '{text}' white={white_pct:.1f}%")

# Also try with inverted (dark text on lighter background)
print("\n=== Inverted threshold (dark text) y=400-520 ===")
for y_start in range(400, 520, 10):
    crop = img[y_start:y_start+40, 150:570]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    white_pct = np.mean(thresh > 0) * 100
    if white_pct > 5 and white_pct < 80:
        text = pytesseract.image_to_string(
            thresh, config="--psm 7"
        ).strip()
        if text:
            print(f"  y={y_start}-{y_start+40}: '{text}' white={white_pct:.1f}%")

# Try green channel isolation (PPPoker pot text might be green-ish)
print("\n=== Color channel isolation y=420-500 ===")
crop_area = img[420:500, 100:620]
for ch_name, ch_idx in [("blue", 0), ("green", 1), ("red", 2)]:
    ch = crop_area[:, :, ch_idx]
    _, thresh = cv2.threshold(ch, 180, 255, cv2.THRESH_BINARY)
    white_pct = np.mean(thresh > 0) * 100
    text = pytesseract.image_to_string(
        thresh, config="--psm 6"
    ).strip()
    print(f"  {ch_name}: text='{text}' white={white_pct:.1f}%")

# Try upscaling for better OCR
print("\n=== Upscaled 3x y=430-480 ===")
crop_pot = img[430:480, 150:570]
gray = cv2.cvtColor(crop_pot, cv2.COLOR_BGR2GRAY)
upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
for thresh_val in [120, 150, 180, 200]:
    _, thresh = cv2.threshold(upscaled, thresh_val, 255, cv2.THRESH_BINARY)
    text = pytesseract.image_to_string(
        thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,Pot"
    ).strip()
    if text:
        print(f"  thresh={thresh_val}: '{text}'")
    # Also try OTSU
_, otsu = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
text_otsu = pytesseract.image_to_string(
    otsu, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,Pot"
).strip()
print(f"  OTSU: '{text_otsu}'")

# Save the pot area for inspection
cv2.imwrite("reports/debug_pot_area.png", img[400:520, 100:620])
print("\nSaved debug_pot_area.png")

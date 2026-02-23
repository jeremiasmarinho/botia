"""Debug OCR pipeline on pot region using TitanOCR preprocessing."""
import subprocess
import numpy as np
import cv2
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.vision_ocr import TitanOCR

adb = r"F:\LDPlayer\LDPlayer9\adb.exe"
r = subprocess.run([adb, "-s", "emulator-5554", "exec-out", "screencap", "-p"], capture_output=True)
buf = np.frombuffer(r.stdout, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
print(f"Frame: {img.shape[1]}x{img.shape[0]}")

ocr = TitanOCR(tesseract_cmd=r"C:\Program Files\Tesseract-OCR\tesseract.exe")

# Pot region: (200, 430, 320, 45)
pot_crop = img[430:475, 200:520]
cv2.imwrite("reports/debug_pot_crop_raw.png", pot_crop)
print(f"Pot crop shape: {pot_crop.shape}")

# TitanOCR preprocessing
preprocessed = ocr._preprocess(pot_crop)
if preprocessed is not None:
    cv2.imwrite("reports/debug_pot_crop_preprocessed.png", preprocessed)
    print(f"Preprocessed shape: {preprocessed.shape}")

# Read numeric value via TitanOCR
value = ocr.read_numeric_region(pot_crop, key="pot", fallback=0.0)
print(f"TitanOCR pot value: {value}")

# Also try direct pytesseract on the crop with various configs
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Save multiple preprocessing variants
gray = cv2.cvtColor(pot_crop, cv2.COLOR_BGR2GRAY)
cv2.imwrite("reports/debug_pot_gray.png", gray)

# Upscale 3x
up = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

for thresh_val in [100, 120, 140, 160, 180, 200]:
    _, t = cv2.threshold(up, thresh_val, 255, cv2.THRESH_BINARY)
    text = pytesseract.image_to_string(t, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,").strip()
    text_full = pytesseract.image_to_string(t, config="--psm 7").strip()
    print(f"  thresh={thresh_val}: digits='{text}' full='{text_full}'")

# OTSU
_, otsu = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
text_otsu = pytesseract.image_to_string(otsu, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,").strip()
text_otsu_full = pytesseract.image_to_string(otsu, config="--psm 7").strip()
print(f"  OTSU: digits='{text_otsu}' full='{text_otsu_full}'")
cv2.imwrite("reports/debug_pot_otsu.png", otsu)

# Try with inverted
_, inv = cv2.threshold(up, 160, 255, cv2.THRESH_BINARY_INV)
text_inv = pytesseract.image_to_string(inv, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,").strip()
text_inv_full = pytesseract.image_to_string(inv, config="--psm 7").strip()
print(f"  INV: digits='{text_inv}' full='{text_inv_full}'")

# Try green channel isolation (PPPoker pot text might be colored)
channels = cv2.split(pot_crop)
for ch_name, ch in zip(["blue", "green", "red"], channels):
    up_ch = cv2.resize(ch, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, t = cv2.threshold(up_ch, 160, 255, cv2.THRESH_BINARY)
    text = pytesseract.image_to_string(t, config="--psm 7").strip()
    if text:
        print(f"  {ch_name} channel: '{text}'")

print("\nDebug images saved to reports/")

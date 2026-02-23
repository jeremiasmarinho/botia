"""Scan candidate pot regions on ADB screenshot."""
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

regions_to_test = {
    "pot_current": (240, 300, 240, 50),
    "center_200": (150, 200, 420, 60),
    "center_250": (150, 250, 420, 60),
    "center_300": (150, 300, 420, 60),
    "center_350": (150, 350, 420, 60),
    "center_400": (150, 400, 420, 60),
    "center_450": (150, 450, 420, 60),
    "center_500": (150, 500, 420, 60),
    "center_550": (150, 550, 420, 60),
    "center_600": (150, 600, 420, 60),
    "center_650": (150, 650, 420, 60),
    "center_700": (150, 700, 420, 60),
    "center_750": (150, 750, 420, 60),
    "center_800": (150, 800, 420, 60),
    "top_area": (200, 50, 320, 40),
    "top_area2": (200, 90, 320, 40),
    "top_area3": (200, 130, 320, 40),
    "top_area4": (200, 170, 320, 40),
}

for name, (x, y, w, h) in regions_to_test.items():
    crop = img[y:y+h, x:x+w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    white_pct = np.mean(thresh > 0) * 100
    text = pytesseract.image_to_string(
        thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,POTpot"
    ).strip()
    if text or white_pct > 8:
        print(f"  {name} ({x},{y},{w},{h}): text='{text}' white={white_pct:.1f}%")

# Also do a full Y-scan for any text in center strip
print("\n--- Full Y-scan (x=200..520) ---")
for y_start in range(40, 900, 30):
    crop = img[y_start:y_start+30, 200:520]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    white_pct = np.mean(thresh > 0) * 100
    if white_pct > 8:
        text = pytesseract.image_to_string(
            thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789.$,POTpot"
        ).strip()
        print(f"  y={y_start}-{y_start+30}: text='{text}' white={white_pct:.1f}%")

# Gold text detection (pot in PPPoker often shown in gold/yellow)
print("\n--- Gold/Yellow text scan ---")
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
# Gold/yellow: H=15-35, S=100-255, V=150-255
gold_mask = cv2.inRange(hsv, (15, 100, 150), (35, 255, 255))
for y_start in range(40, 900, 30):
    strip = gold_mask[y_start:y_start+30, 100:620]
    gold_pct = np.mean(strip > 0) * 100
    if gold_pct > 2:
        print(f"  y={y_start}-{y_start+30}: gold={gold_pct:.1f}%")

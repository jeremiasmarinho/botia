"""Scan bottom of screenshot to find text positions precisely."""
import cv2
import numpy as np

frame = cv2.imread("reports/screenshot_clean.png")
h, w = frame.shape[:2]
print(f"Frame: {w}x{h}")

# Scan precise strips around known areas
print()
print("Precise scan (canvas y=850-960, x=100-450):")
try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    
    for cy in range(850, 960, 5):
        strip = frame[cy:cy+18, 100:450]
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        text = pytesseract.image_to_string(gray, config="--psm 7").strip()
        if text:
            android_y = int(cy * 1280 / h)
            print(f"  canvas_y={cy:4d} → android_y={android_y:4d}: \"{text}\"")
    
    # Wider scan of each major strip
    print()
    print("Full width scan (canvas y=850-990, strips of 25px):")
    for cy in range(850, 990, 15):
        strip = frame[cy:cy+25, :]
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        text = pytesseract.image_to_string(gray, config="--psm 7").strip()
        if text:
            android_y = int(cy * 1280 / h)
            print(f"  canvas_y={cy:4d}-{cy+25:4d} → android_y={android_y:4d}: \"{text}\"")

    # Specifically scan for the stack number with digit-only whitelist
    print()
    print("Digit-only scan (hero area, x=150-350):")
    for cy in range(870, 930, 3):
        strip = frame[cy:cy+22, 150:350]
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
        text = pytesseract.image_to_string(
            thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789."
        ).strip()
        if text:
            android_y = int(cy * 1280 / h)
            print(f"  canvas_y={cy:4d} → android_y={android_y:4d}: \"{text}\" (digits from x=150-350)")
    
    # Scan button area for call amount
    print()
    print("Button area scan (y=940-990):")
    for cy in range(935, 990, 5):
        for section, sx, ex in [("left", 0, 180), ("center", 180, 380), ("right", 380, 562)]:
            strip = frame[cy:cy+22, sx:ex]
            gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
            text = pytesseract.image_to_string(gray, config="--psm 7").strip()
            if text:
                android_y = int(cy * 1280 / h)
                print(f"  canvas_y={cy:4d} {section:>6s} (x={sx}-{ex}) → android_y={android_y:4d}: \"{text}\"")

except Exception as e:
    print(f"Error: {e}")

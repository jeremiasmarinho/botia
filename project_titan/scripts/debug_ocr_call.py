"""Find call button text location precisely."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cv2, numpy as np
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

os.makedirs("reports/ocr_debug4", exist_ok=True)

# Load all frames
frames = []
for c in "ABCDEF":
    path = os.path.join(os.path.dirname(__file__), "..", "..", f"screen{c}.png")
    if os.path.exists(path):
        img = cv2.imread(path)
        if img is not None:
            frames.append((f"screen{c}", cv2.resize(img, (720, 1280))))

# Call button center: (361, 1220)
# Check a wider area around the call button
print("=== CALL BUTTON AREA ANALYSIS ===")
for fname, frame in frames[:1]:  # Just first frame
    # Save wide call area
    call_wide = frame[1170:1260, 240:480]
    cv2.imwrite("reports/ocr_debug4/call_wide.png", call_wide)
    
    # Pixel grid
    print(f"\n{fname} - Call area pixel colors (y=1180-1250, x=270-460, step=10):")
    for y in range(1180, 1255, 5):
        for x in [310, 330, 350, 370, 390, 410]:
            b, g, r = frame[y, x]
            h, s, v = cv2.cvtColor(np.array([[[b,g,r]]], dtype=np.uint8), cv2.COLOR_BGR2HSV)[0,0]
            gray = int(0.299*r + 0.587*g + 0.114*b)
            print(f"  ({x},{y}): BGR=({b:3d},{g:3d},{r:3d}) HSV=({h:3d},{s:3d},{v:3d}) gray={gray:3d}")
        print()

# Try many sub-regions around call button
print("=== TRYING CALL SUB-REGIONS ===")
call_regions = [
    ("call_center",    (310, 1200, 110, 35)),
    ("call_wide",      (270, 1190, 180, 50)),
    ("call_above",     (310, 1180, 110, 30)),
    ("call_below",     (310, 1210, 110, 30)),
    ("call_text",      (310, 1205, 110, 25)),
    ("fold_btn",       (75, 1200, 110, 35)),
    ("raise_btn",      (540, 1200, 110, 35)),
    ("btn_row_text",   (60, 1195, 600, 40)),
]

for fname, frame in frames:
    tag = fname[-1]
    results = []
    for name, (x, y, w, h) in call_regions:
        crop = frame[y:y+h, x:x+w]
        
        # Check colors
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        
        # Various masks
        yellow = cv2.inRange(hsv, (10, 80, 80), (35, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 200), (180, 40, 255))
        bright = cv2.inRange(hsv, (0, 0, 150), (180, 255, 255))
        n_y = int(np.sum(yellow > 0))
        n_w = int(np.sum(white > 0))
        n_b = int(np.sum(bright > 0))
        total = w * h
        avg_gray = float(np.mean(gray))
        
        # OCR
        ch, cw = crop.shape[:2]
        up = cv2.resize(gray, (cw*6, ch*6), interpolation=cv2.INTER_CUBIC)
        _, otsu = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        for img_name, img in [("otsu", otsu), ("otsu_inv", cv2.bitwise_not(otsu))]:
            config = "--psm 7 -c tessedit_char_whitelist=0123456789.$,Kk"
            text = pytesseract.image_to_string(img, config=config).strip()
            if text:
                results.append(f"{name}_{img_name}={text}")
        
        # Save first frame crops
        if tag == 'A':
            cv2.imwrite(f"reports/ocr_debug4/{name}.png", crop)
    
    if results:
        print(f"  {fname}: {', '.join(results[:6])}")
    else:
        print(f"  {fname}: (no text)")

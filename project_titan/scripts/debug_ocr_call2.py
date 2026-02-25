"""Check for call amount display between stack and buttons."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cv2, numpy as np
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

os.makedirs("reports/ocr_debug5", exist_ok=True)

frames = []
for c in "ABCDEF":
    path = os.path.join(os.path.dirname(__file__), "..", "..", f"screen{c}.png")
    if os.path.exists(path):
        img = cv2.imread(path)
        if img is not None:
            frames.append((f"screen{c}", cv2.resize(img, (720, 1280))))

# Check area between stack and buttons: y=1060-1190
print("=== AREA BETWEEN STACK AND BUTTONS ===")
for fname, frame in frames[:1]:  # Just screenA
    # Save the full area
    cv2.imwrite("reports/ocr_debug5/between.png", frame[1060:1200, 50:670])
    
    # Scan for ANY text-like pixels (bright or high contrast)
    for y_start in range(1060, 1200, 10):
        row = frame[y_start:y_start+10, 50:670]
        gray = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
        
        mean_g = float(np.mean(gray))
        max_g = int(np.max(gray))
        
        # Check for bright text
        bright = np.sum(gray > 150)
        # Check for yellow text
        yellow = np.sum(cv2.inRange(hsv, (10, 80, 80), (35, 255, 255)) > 0)
        # Check for white text
        white = np.sum(cv2.inRange(hsv, (0, 0, 200), (180, 40, 255)) > 0)
        
        total = row.shape[0] * row.shape[1]
        
        if bright > 20 or yellow > 10 or white > 10:
            # Find brightest pixel location
            ys, xs = np.where(gray > max(mean_g + 30, 100))
            if len(ys) > 0:
                b, g, r = row[ys[0], xs[0]]
                h, s, v = hsv[ys[0], xs[0]]
                print(f"  y={y_start:4d}: mean={mean_g:.0f} max={max_g} "
                      f"bright={bright:4d} yellow={yellow:3d} white={white:3d}  "
                      f"sample({xs[0]+50},{ys[0]+y_start}): "
                      f"BGR=({b},{g},{r}) HSV=({h},{s},{v})")

# Check for bet/raise slider area (typically above action buttons)
print("\n=== ACTION AREA SCAN (y=1100-1200, x=50-670) ===")
for fname, frame in frames[:2]:
    print(f"\n{fname}:")
    # Try OCR on various sub-regions
    regions = [
        ("bet_display",  (200, 1100, 320, 40)),
        ("slider_area",  (200, 1140, 320, 40)),
        ("above_btns",   (100, 1160, 520, 30)),
        ("call_label",   (280, 1195, 160, 50)),  # The actual green button
    ]
    
    for name, (x, y, w, h) in regions:
        crop = frame[y:y+h, x:x+w]
        cv2.imwrite(f"reports/ocr_debug5/{name}_{fname[-1]}.png", crop)
        
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        mean_g = float(np.mean(gray))
        
        # Enhanced CLAHE for low contrast
        ch, cw = gray.shape[:2]
        up = cv2.resize(gray, (cw*6, ch*6), interpolation=cv2.INTER_CUBIC)
        clahe = cv2.createCLAHE(clipLimit=5.0, tileGridSize=(4, 4))
        enhanced = clahe.apply(up)
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        text_normal = ""
        text_inv = ""
        for psm in [7, 6]:
            config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,Kk"
            t = pytesseract.image_to_string(otsu, config=config).strip()
            t_inv = pytesseract.image_to_string(cv2.bitwise_not(otsu), config=config).strip()
            if t and not text_normal: text_normal = f"psm{psm}={t}"
            if t_inv and not text_inv: text_inv = f"psm{psm}_inv={t_inv}"
        
        result_parts = [p for p in [text_normal, text_inv] if p]
        if result_parts:
            print(f"  {name:15s}: mean_gray={mean_g:.0f}  {', '.join(result_parts)}")
        else:
            print(f"  {name:15s}: mean_gray={mean_g:.0f}  (no text)")

# Try to OCR the call button with EXTREME contrast enhancement
print("\n=== CALL BUTTON WITH EXTREME ENHANCEMENT ===")
for fname, frame in frames:
    # Call button area - where the slightly brighter text is
    crop = frame[1210:1240, 300:420]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    ch, cw = gray.shape[:2]
    up = cv2.resize(gray, (cw*8, ch*8), interpolation=cv2.INTER_CUBIC)
    
    # Very aggressive CLAHE
    clahe = cv2.createCLAHE(clipLimit=10.0, tileGridSize=(2, 2))
    enhanced = clahe.apply(up)
    _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # Also try fixed threshold at different levels
    results = {}
    for psm in [7, 6]:
        config = f"--psm {psm} -c tessedit_char_whitelist=0123456789.$,KkCcall"
        text = pytesseract.image_to_string(otsu, config=config).strip()
        if text: results[f"clahe_psm{psm}"] = text
        text_inv = pytesseract.image_to_string(cv2.bitwise_not(otsu), config=config).strip()
        if text_inv: results[f"clahe_inv_psm{psm}"] = text_inv
    
    if results:
        print(f"  {fname}: {', '.join(f'{k}={v}' for k, v in results.items())}")
    else:
        print(f"  {fname}: (no text)")
    
    if fname == "screenA":
        cv2.imwrite("reports/ocr_debug5/call_enhanced.png", enhanced)
        cv2.imwrite("reports/ocr_debug5/call_otsu.png", otsu)

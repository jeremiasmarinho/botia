"""Try grayscale approaches on tight stack regions + analyze the image."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cv2, numpy as np
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

os.makedirs("reports/ocr_debug3", exist_ok=True)

frame_files = [f"screen{c}.png" for c in "ABCDEF"]
frames = []
for fname in frame_files:
    path = os.path.join(os.path.dirname(__file__), "..", "..", fname)
    if os.path.exists(path):
        img = cv2.imread(path)
        if img is not None:
            frames.append((fname, cv2.resize(img, (720, 1280))))

# Save full stack area crops for visual inspection
for fname, frame in frames:
    tag = fname[6]  # A, B, C, etc
    # Wide stack area
    crop = frame[1000:1070, 390:620]
    cv2.imwrite(f"reports/ocr_debug3/stack_wide_{tag}.png", crop)
    # Tight stack
    crop = frame[1028:1058, 430:590]
    cv2.imwrite(f"reports/ocr_debug3/stack_tight_{tag}.png", crop)
    # Very tight
    crop = frame[1032:1052, 440:580]
    cv2.imwrite(f"reports/ocr_debug3/stack_vtight_{tag}.png", crop)
    
    # Pot area
    crop_pot = frame[100:220, 260:460]
    cv2.imwrite(f"reports/ocr_debug3/pot_area_{tag}.png", crop_pot)
    
    # Call button area
    crop_call = frame[1180:1260, 270:450]
    cv2.imwrite(f"reports/ocr_debug3/call_btn_{tag}.png", crop_call)

# Test OCR on very-tight stack with different methods
print("=== VERY TIGHT STACK (440, 1032, 140, 20) ===")
for fname, frame in frames:
    crop = frame[1032:1052, 440:580]
    ch, cw = crop.shape[:2]
    
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    # Try high upscale + OTSU
    up8 = cv2.resize(gray, (cw*8, ch*8), interpolation=cv2.INTER_CUBIC)
    _, otsu = cv2.threshold(up8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # Try different fixed thresholds
    results = {}
    for thresh in [200, 180, 160]:
        _, fixed = cv2.threshold(up8, thresh, 255, cv2.THRESH_BINARY)
        config = f"--psm 7 -c tessedit_char_whitelist=0123456789.$,Kk"
        text = pytesseract.image_to_string(fixed, config=config).strip()
        text_inv = pytesseract.image_to_string(cv2.bitwise_not(fixed), config=config).strip()
        if text: results[f"thr{thresh}"] = text
        if text_inv: results[f"thr{thresh}_inv"] = text_inv
    
    config = f"--psm 7 -c tessedit_char_whitelist=0123456789.$,Kk"
    text = pytesseract.image_to_string(otsu, config=config).strip()
    text_inv = pytesseract.image_to_string(cv2.bitwise_not(otsu), config=config).strip()
    if text: results["otsu"] = text
    if text_inv: results["otsu_inv"] = text_inv
    
    # Mean/min/max gray
    print(f"  {fname}: gray mean={np.mean(gray):.0f} min={np.min(gray)} max={np.max(gray)}  "
          f"| {', '.join(f'{k}={v}' for k, v in results.items())}")

# Check what the actual hero stack region looks like on the screen
print("\n=== HERO PANEL ANALYSIS ===")
for fname, frame in frames[:1]:  # Just first frame
    print(f"\n{fname}:")
    # Scan below hero cards (y=1000-1100) for text
    for y_start in range(1000, 1100, 5):
        row = frame[y_start:y_start+5, 380:620]
        gray_r = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
        mean_val = float(np.mean(gray_r))
        max_val = int(np.max(gray_r))
        n_bright = int(np.sum(gray_r > 200))
        total = row.shape[0] * row.shape[1]
        if n_bright > 0 or mean_val > 100:
            hsv_r = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
            # Sample brightest pixel
            ys, xs = np.where(gray_r > max(mean_val, 150))
            if len(ys) > 0:
                b, g, r = row[ys[0], xs[0]]
                h, s, v = hsv_r[ys[0], xs[0]]
                print(f"  y={y_start:4d}: mean={mean_val:5.0f} max={max_val:3d} "
                      f"bright200={n_bright:3d}/{total}  "
                      f"sample BGR=({b},{g},{r}) HSV=({h},{s},{v})")

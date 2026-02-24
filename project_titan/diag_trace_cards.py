#!/usr/bin/env python3
"""Trace card reading step by step to find why PPPokerCardReader returns empty."""
from __future__ import annotations
import ctypes, os, re, sys
try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
except: pass

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

frame = cv2.imread('reports/diag_frame_raw_20260224_064631.png')
print(f'Frame: {frame.shape}')

button_y = 1262
hero_y1 = max(0, button_y - 420)
hero_y2 = min(1280, button_y - 150)
hero_x1, hero_x2 = 101, 621
hero_crop = frame[hero_y1:hero_y2, hero_x1:hero_x2]
print(f'Hero crop: {hero_crop.shape} y=[{hero_y1},{hero_y2}]')

gray = cv2.cvtColor(hero_crop, cv2.COLOR_BGR2GRAY)
h_r, w_r = hero_crop.shape[:2]

# Auto-crop
_, bright_full = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
row_brightness = np.mean(bright_full > 0, axis=1)
bright_rows = np.where(row_brightness > 0.05)[0]
if len(bright_rows) > 0:
    y_top = max(0, int(bright_rows[0]) - 10)
    y_bot = min(h_r, int(bright_rows[-1]) + 10)
    if (y_bot - y_top) < h_r * 0.85:
        working = hero_crop[y_top:y_bot, :]
        working_gray = gray[y_top:y_bot, :]
        print(f'Auto-cropped: {working.shape} (saved {(1-(y_bot-y_top)/h_r)*100:.0f}%)')
    else:
        working = hero_crop
        working_gray = gray
else:
    working = hero_crop
    working_gray = gray

# Contour detection with splitting
_, mask = cv2.threshold(working_gray, 140, 255, cv2.THRESH_BINARY)
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

bboxes = []
for c in contours:
    x, y, w, h = cv2.boundingRect(c)
    if w < 30 or h < 45 or h > 150:
        continue
    aspect = w / max(h, 1)
    if aspect < 0.3:
        continue
    if w > 120 * 1.3:
        n = max(2, round(w / 55))
        card_w = w // n
        for i in range(n):
            cx = x + i * card_w
            cw = card_w if i < n-1 else (x + w - cx)
            bboxes.append((cx, y, cw, h))
    elif aspect <= 0.95:
        bboxes.append((x, y, w, h))

bboxes.sort(key=lambda b: b[0])
print(f'Card bboxes: {len(bboxes)}')

# Read each card
try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    HAS_TESS = True
except:
    HAS_TESS = False
    print("Tesseract not available")

for i, (x, y, w, h) in enumerate(bboxes):
    card_crop = working[y:y+h, x:x+w]
    print(f'\nCard {i}: ({x},{y}) {w}x{h}')
    cv2.imwrite(f'reports/hero_card_test_{i}.png', card_crop)

    rank_h = max(10, int(h * 0.55))
    rank_region = card_crop[0:rank_h, :]
    cv2.imwrite(f'reports/hero_rank_test_{i}.png', rank_region)

    # OCR
    rank = None
    if HAS_TESS:
        try:
            rg = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
            scale = max(1, min(4, 80 // max(rg.shape[0], 1)))
            if scale > 1:
                rg = cv2.resize(rg, (rg.shape[1]*scale, rg.shape[0]*scale), interpolation=cv2.INTER_CUBIC)
            _, binary = cv2.threshold(rg, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            white_frac = float(np.mean(binary > 127))
            if white_frac < 0.5:
                binary = cv2.bitwise_not(binary)
            cv2.imwrite(f'reports/hero_binary_test_{i}.png', binary)

            for psm in (10, 7, 8):
                config = f'--psm {psm} -c tessedit_char_whitelist=AaKkQqJjTt0123456789'
                text = pytesseract.image_to_string(binary, config=config).strip()
                text = re.sub(r'[^AaKkQqJjTt0-9]', '', text)
                if text:
                    print(f'  OCR PSM={psm}: "{text}"')
                    rank_map = {"A":"A","a":"A","K":"K","k":"K","Q":"Q","q":"Q","J":"J","j":"J",
                                "T":"T","t":"T","10":"T","9":"9","8":"8","7":"7","6":"6",
                                "5":"5","4":"4","3":"3","2":"2","1":"A","0":"T"}
                    for ch in text:
                        if ch in rank_map:
                            rank = rank_map[ch]
                            break
                    if rank:
                        break
        except Exception as e:
            print(f'  OCR error: {e}')
    
    # HSV suit
    hsv = cv2.cvtColor(rank_region, cv2.COLOR_BGR2HSV)
    h_ch = hsv[:,:,0]
    s_ch = hsv[:,:,1]
    v_ch = hsv[:,:,2]
    non_bg = (s_ch > 25) & (v_ch > 30) & (v_ch < 240)
    n_col = int(np.sum(non_bg))
    dark_mask = (v_ch < 60) & (s_ch < 80)
    n_dark = int(np.sum(dark_mask))
    min_px = max(10, int(rank_region.shape[0] * rank_region.shape[1] * 0.02))
    
    suit = None
    if n_col >= min_px:
        hue_vals = h_ch[non_bg]
        sat_vals = s_ch[non_bg]
        felt = (hue_vals > 70) & (hue_vals < 86) & (sat_vals > 130)
        red = ((hue_vals < 12) | (hue_vals > 158)) & (sat_vals > 40) & ~felt
        blue = (hue_vals > 90) & (hue_vals < 140) & (sat_vals > 35) & ~felt
        green = (hue_vals > 35) & (hue_vals < 85) & (sat_vals > 35) & ~felt
        n_red, n_blue, n_green = int(np.sum(red)), int(np.sum(blue)), int(np.sum(green))
        n_felt = int(np.sum(felt))
        print(f'  HSV: col={n_col} dark={n_dark} min_px={min_px}')
        print(f'  Red(♥)={n_red} Blue(♦)={n_blue} Green(♣)={n_green} Felt={n_felt}')
        
        # Hue histogram
        for bucket in range(0, 181, 10):
            cnt = int(np.sum((hue_vals >= bucket) & (hue_vals < bucket+10)))
            if cnt > 0:
                print(f'    H[{bucket}-{bucket+10}]={cnt}', end='')
        print()
        
        counts = {"h": n_red, "d": n_blue, "c": n_green}
        best = max(counts, key=lambda k: counts[k])
        if counts[best] >= min_px:
            suit = best
    if suit is None and n_dark >= min_px:
        suit = "s"
    if suit is None:
        gray_s = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
        dark_text = (gray_s < 70) & (gray_s > 5)
        if int(np.sum(dark_text)) >= min_px:
            suit = "s"
    
    print(f'  Rank={rank} Suit={suit} Token={"" if not rank or not suit else rank+suit}')

# Also test board
print("\n" + "="*60)
print("BOARD")
print("="*60)
pot_y = 504
board_y1 = max(0, pot_y - 40)
board_y2 = min(1280, pot_y + 200)
board_x1 = max(0, 360 - 260)
board_x2 = min(720, 360 + 260)
board_crop = frame[board_y1:board_y2, board_x1:board_x2]
print(f'Board crop: {board_crop.shape} y=[{board_y1},{board_y2}]')

bgray = cv2.cvtColor(board_crop, cv2.COLOR_BGR2GRAY)
_, bmask = cv2.threshold(bgray, 140, 255, cv2.THRESH_BINARY)
bkernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
bmask = cv2.morphologyEx(bmask, cv2.MORPH_CLOSE, bkernel)
bcontours, _ = cv2.findContours(bmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

bbboxes = []
for c in bcontours:
    x, y, w, h = cv2.boundingRect(c)
    if w < 30 or h < 45 or h > 150:
        continue
    aspect = w / max(h, 1)
    if aspect < 0.3 or aspect > 0.95:
        continue
    bbboxes.append((x, y, w, h))
    print(f'  Board card: ({x},{y}) {w}x{h} aspect={aspect:.2f}')

bbboxes.sort(key=lambda b: b[0])
for i, (x, y, w, h) in enumerate(bbboxes):
    card_crop = board_crop[y:y+h, x:x+w]
    cv2.imwrite(f'reports/board_card_test_{i}.png', card_crop)
    rank_h = max(10, int(h * 0.55))
    rank_region = card_crop[0:rank_h, :]
    
    # Quick suit analysis
    hsv = cv2.cvtColor(rank_region, cv2.COLOR_BGR2HSV)
    h_ch = hsv[:,:,0]
    s_ch = hsv[:,:,1]
    v_ch = hsv[:,:,2]
    non_bg = (s_ch > 25) & (v_ch > 30) & (v_ch < 240)
    n_col = int(np.sum(non_bg))
    min_px = max(10, int(rank_region.shape[0] * rank_region.shape[1] * 0.02))
    
    if n_col >= min_px:
        hue_vals = h_ch[non_bg]
        sat_vals = s_ch[non_bg]
        felt = (hue_vals > 70) & (hue_vals < 86) & (sat_vals > 130)
        red = ((hue_vals < 12) | (hue_vals > 158)) & (sat_vals > 40) & ~felt
        blue = (hue_vals > 90) & (hue_vals < 140) & (sat_vals > 35) & ~felt
        green = (hue_vals > 35) & (hue_vals < 85) & (sat_vals > 35) & ~felt
        print(f'  Board {i}: Red={int(np.sum(red))} Blue={int(np.sum(blue))} Green={int(np.sum(green))} Felt={int(np.sum(felt))}')
        for bucket in range(0, 181, 10):
            cnt = int(np.sum((hue_vals >= bucket) & (hue_vals < bucket+10)))
            if cnt > 0:
                print(f'    H[{bucket}-{bucket+10}]={cnt}', end='')
        print()

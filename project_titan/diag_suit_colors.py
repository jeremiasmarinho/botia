#!/usr/bin/env python3
"""Diagnostic: analyse HSV suit colours on MuMu Player 12.

Captures a live frame from the MuMu emulator, finds card regions via
the same pipeline as PPPokerCardReader, and dumps detailed HSV colour
stats for each card — showing exactly why suits are being misidentified.

Run with PPPoker open and cards visible on screen:
    cd project_titan
    python diag_suit_colors.py

Outputs:
    reports/diag_suit_hsv_<timestamp>.png   — annotated frame
    reports/diag_suit_cards_<timestamp>/     — individual card crops + HSV maps
    Console: per-card HSV breakdown + current vs expected suit
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── DPI awareness (must be FIRST, before any Win32 calls) ──────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import cv2
import numpy as np
import mss

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def capture_frame() -> np.ndarray | None:
    """Capture frame from MuMu nemuwin, resize to 720x1280."""
    from utils.emulator_profiles import get_profile, find_render_hwnd

    profile = get_profile()
    hwnd = find_render_hwnd(profile)
    if not hwnd:
        print("[ERRO] nemuwin não encontrado")
        return None

    user32 = ctypes.windll.user32

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    cw, ch = rect.right, rect.bottom
    print(f"[INFO] nemuwin client area: {cw}x{ch}")

    if cw <= 0 or ch <= 0:
        return None

    with mss.mss() as sct:
        monitor = {"left": pt.x, "top": pt.y, "width": cw, "height": ch}
        raw = np.array(sct.grab(monitor))

    frame = raw[:, :, :3].copy()  # BGRA → BGR
    if frame.shape[1] != 720 or frame.shape[0] != 1280:
        frame = cv2.resize(frame, (720, 1280), interpolation=cv2.INTER_LINEAR)
    print(f"[INFO] Frame shape: {frame.shape}")
    return frame


def analyse_hsv_region(region: np.ndarray, label: str) -> dict:
    """Analyse HSV colour distribution of a card region.
    
    Returns dict with detailed stats matching PPPokerCardReader logic.
    """
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    h_ch = hsv[:, :, 0]
    s_ch = hsv[:, :, 1]
    v_ch = hsv[:, :, 2]

    # Same masks as PPPokerCardReader._detect_suit_color
    non_bg = (s_ch > 40) & (v_ch > 30) & (v_ch < 240)
    dark_mask = (v_ch < 60) & (s_ch < 80)

    n_coloured = int(np.sum(non_bg))
    n_dark = int(np.sum(dark_mask))
    total_pixels = region.shape[0] * region.shape[1]
    min_pixels = max(10, int(total_pixels * 0.02))

    stats = {
        "label": label,
        "shape": region.shape[:2],
        "total_pixels": total_pixels,
        "min_pixels": min_pixels,
        "n_coloured": n_coloured,
        "n_dark": n_dark,
        "detected_suit": None,
        "red_count": 0,
        "blue_count": 0,
        "green_count": 0,
    }

    if n_coloured >= min_pixels:
        hue_vals = h_ch[non_bg]
        sat_vals = s_ch[non_bg]

        # Red (hearts): H < 12 or H > 158, S > 60
        red_mask = ((hue_vals < 12) | (hue_vals > 158)) & (sat_vals > 60)
        n_red = int(np.sum(red_mask))

        # Blue (diamonds): H in (95, 135), S > 50
        blue_mask = (hue_vals > 95) & (hue_vals < 135) & (sat_vals > 50)
        n_blue = int(np.sum(blue_mask))

        # Green (clubs): H in (35, 85), S > 50
        green_mask = (hue_vals > 35) & (hue_vals < 85) & (sat_vals > 50)
        n_green = int(np.sum(green_mask))

        stats["red_count"] = n_red
        stats["blue_count"] = n_blue
        stats["green_count"] = n_green

        counts = {"h": n_red, "d": n_blue, "c": n_green}
        best_suit = max(counts, key=lambda k: counts[k])
        best_count = counts[best_suit]

        if best_count >= min_pixels:
            stats["detected_suit"] = best_suit
        elif n_dark >= min_pixels:
            stats["detected_suit"] = "s"
        else:
            # Last resort dark text check
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            dark_text = (gray < 100) & (gray > 5)
            if int(np.sum(dark_text)) >= min_pixels:
                stats["detected_suit"] = "s"
    elif n_dark >= min_pixels:
        stats["detected_suit"] = "s"
    else:
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        dark_text = (gray < 100) & (gray > 5)
        if int(np.sum(dark_text)) >= min_pixels:
            stats["detected_suit"] = "s"

    # ── Extended HSV stats for debugging ──
    if n_coloured > 0:
        hue_vals = h_ch[non_bg]
        sat_vals = s_ch[non_bg]
        val_vals = v_ch[non_bg]
        stats["hue_mean"] = float(np.mean(hue_vals))
        stats["hue_median"] = float(np.median(hue_vals))
        stats["hue_std"] = float(np.std(hue_vals))
        stats["sat_mean"] = float(np.mean(sat_vals))
        stats["sat_median"] = float(np.median(sat_vals))
        stats["val_mean"] = float(np.mean(val_vals))
        stats["val_median"] = float(np.median(val_vals))
        
        # Histogram of hue values (binned in 10-degree increments)
        hue_hist = {}
        for bucket_start in range(0, 181, 10):
            bucket_end = bucket_start + 10
            count = int(np.sum((hue_vals >= bucket_start) & (hue_vals < bucket_end)))
            if count > 0:
                hue_hist[f"H{bucket_start}-{bucket_end}"] = count
        stats["hue_histogram"] = hue_hist
    else:
        stats["hue_mean"] = None

    return stats


def find_card_contours(region: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Find bright rectangular card shapes (same logic as PPPokerCardReader)."""
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    _, bright_mask = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    bboxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 30 or w > 120:
            continue
        if h < 45 or h > 150:
            continue
        aspect = w / max(h, 1)
        if aspect < 0.3 or aspect > 0.95:
            continue
        bboxes.append((x, y, w, h))

    bboxes.sort(key=lambda b: b[0])
    return bboxes


def run_yolo(frame: np.ndarray) -> list[dict]:
    """Run YOLO model and return detections."""
    try:
        from ultralytics import YOLO
        model_path = "models/titan_v8_pro.pt"
        if not os.path.exists(model_path):
            print(f"[WARN] Modelo YOLO não encontrado: {model_path}")
            return []
        model = YOLO(model_path)
        results = model.predict(source=frame, verbose=False, conf=0.05)
        if not results:
            return []
        
        detections = []
        r = results[0]
        names = getattr(r, "names", {})
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            return []
        
        cls_vals = boxes.cls.tolist() if boxes.cls is not None else []
        xyxy_vals = boxes.xyxy.tolist() if boxes.xyxy is not None else []
        conf_vals = boxes.conf.tolist() if boxes.conf is not None else []
        
        for i, (cls_idx, xyxy) in enumerate(zip(cls_vals, xyxy_vals)):
            label = names.get(int(cls_idx), f"cls{int(cls_idx)}")
            conf = float(conf_vals[i]) if i < len(conf_vals) else 0.0
            cx = (xyxy[0] + xyxy[2]) / 2
            cy = (xyxy[1] + xyxy[3]) / 2
            detections.append({
                "label": label,
                "conf": conf,
                "cx": cx, "cy": cy,
                "x1": xyxy[0], "y1": xyxy[1],
                "x2": xyxy[2], "y2": xyxy[3],
            })
        return detections
    except Exception as e:
        print(f"[WARN] YOLO failed: {e}")
        return []


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    cards_dir = out_dir / f"diag_suit_cards_{ts}"
    cards_dir.mkdir(exist_ok=True)

    print("=" * 70)
    print("   DIAGNÓSTICO DE CORES HSV — MuMu Player 12")
    print("=" * 70)

    # ── 1. Capture frame ──
    frame = capture_frame()
    if frame is None:
        print("[ERRO] Falha na captura do frame")
        return

    # ── 2. YOLO detections ──
    print("\n── YOLO Detections ──")
    detections = run_yolo(frame)
    card_dets = []
    action_dets = []
    for d in detections:
        label = d["label"].lower()
        # Check if it's a card (2-char like "4h", "As")
        is_card = len(d["label"]) == 2 and d["label"][0] in "23456789TJQKA" and d["label"][1] in "cdhs"
        if is_card:
            card_dets.append(d)
            zone = "HERO" if d["cy"] > 750 else ("BOARD" if d["cy"] > 400 else "???")
            print(f"  CARD: {d['label']} conf={d['conf']:.3f} pos=({d['cx']:.0f},{d['cy']:.0f}) zone={zone}")
        elif label in ("fold", "check", "call", "raise", "raise_small", "raise_big",
                       "pot", "stack", "bet", "allin"):
            action_dets.append(d)
            print(f"  ACTION: {d['label']} conf={d['conf']:.3f} pos=({d['cx']:.0f},{d['cy']:.0f})")
    
    if not card_dets:
        print("  [!] NENHUMA carta detectada pelo YOLO")
    print(f"  Total: {len(card_dets)} cartas, {len(action_dets)} ações")

    # ── 3. Determine hero/board regions from config ──
    # Config: hero_area y=840 h=260, board_area y=480 h=140
    hero_y1, hero_y2 = 790, 1150  # y-50, y+h+50
    board_y1, board_y2 = 430, 670

    # For card reader: use action_coordinates from config
    # fold=(126,1220), call=(361,1220), raise=(596,1220)
    button_xs = [126, 361, 596]
    button_ys = [1220, 1220, 1220]
    table_center_x = int(sum(button_xs) / len(button_xs))  # 361
    button_y = int(sum(button_ys) / len(button_ys))  # 1220

    # PPPokerCardReader offsets:
    # hero: button_y-420 to button_y-150 = 800 to 1070
    # board (fallback): button_y-760 = 460, then pot offsets -40 to +200
    cr_hero_y1 = max(0, button_y - 420)  # 800
    cr_hero_y2 = min(1280, button_y - 150)  # 1070
    cr_hero_x1 = max(0, table_center_x - 260)  # 101
    cr_hero_x2 = min(720, table_center_x + 260)  # 621

    cr_board_y = max(0, button_y - 760)  # 460
    cr_board_y1 = max(0, cr_board_y - 40)  # 420
    cr_board_y2 = min(1280, cr_board_y + 200)  # 660
    cr_board_x1 = max(0, table_center_x - 260)  # 101
    cr_board_x2 = min(720, table_center_x + 260)  # 621

    print(f"\n── Regiões de Cartas ──")
    print(f"  Hero (CardReader):  y=[{cr_hero_y1},{cr_hero_y2}] x=[{cr_hero_x1},{cr_hero_x2}]")
    print(f"  Board (CardReader): y=[{cr_board_y1},{cr_board_y2}] x=[{cr_board_x1},{cr_board_x2}]")
    print(f"  Hero (YOLO Y):     y=[{hero_y1},{hero_y2}]")
    print(f"  Board (YOLO Y):    y=[{board_y1},{board_y2}]")

    # ── 4. Extract card regions and analyse HSV ──
    annotated = frame.copy()

    # Draw zone lines
    cv2.line(annotated, (0, hero_y1), (720, hero_y1), (0, 255, 0), 1)
    cv2.line(annotated, (0, hero_y2), (720, hero_y2), (0, 255, 0), 1)
    cv2.putText(annotated, "HERO ZONE", (5, hero_y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    cv2.line(annotated, (0, board_y1), (720, board_y1), (255, 0, 0), 1)
    cv2.line(annotated, (0, board_y2), (720, board_y2), (255, 0, 0), 1)
    cv2.putText(annotated, "BOARD ZONE", (5, board_y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

    # Draw card reader regions
    cv2.rectangle(annotated, (cr_hero_x1, cr_hero_y1), (cr_hero_x2, cr_hero_y2), (0, 255, 255), 2)
    cv2.putText(annotated, "CR HERO", (cr_hero_x1+5, cr_hero_y1+20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.rectangle(annotated, (cr_board_x1, cr_board_y1), (cr_board_x2, cr_board_y2), (0, 165, 255), 2)
    cv2.putText(annotated, "CR BOARD", (cr_board_x1+5, cr_board_y1+20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

    # ── 5. Find cards in hero region ──
    print(f"\n{'='*70}")
    print("   ANÁLISE HSV — HERO CARDS")
    print(f"{'='*70}")

    hero_crop = frame[cr_hero_y1:cr_hero_y2, cr_hero_x1:cr_hero_x2]
    cv2.imwrite(str(cards_dir / "hero_region.png"), hero_crop)
    hero_boxes = find_card_contours(hero_crop)
    print(f"  Contornos de cartas encontrados: {len(hero_boxes)}")

    if not hero_boxes:
        # Try with lower threshold
        gray = cv2.cvtColor(hero_crop, cv2.COLOR_BGR2GRAY)
        print(f"  Hero region brightness: mean={np.mean(gray):.1f} max={np.max(gray)} min={np.min(gray)}")
        # Try threshold at 100
        _, bright100 = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
        contours100, _ = cv2.findContours(bright100, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        print(f"  Contornos com threshold=100: {len(contours100)}")
        for c in contours100:
            x, y, w, h = cv2.boundingRect(c)
            if w > 15 and h > 15:
                print(f"    bbox: ({x},{y}) {w}x{h} aspect={w/max(h,1):.2f}")
        cv2.imwrite(str(cards_dir / "hero_bright140.png"), 
                    cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)[1])
        cv2.imwrite(str(cards_dir / "hero_bright100.png"), bright100)
        cv2.imwrite(str(cards_dir / "hero_bright80.png"),
                    cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY)[1])
    
    for i, (x, y, w, h) in enumerate(hero_boxes):
        card_crop = hero_crop[y:y+h, x:x+w]
        rank_h = max(10, int(h * 0.55))
        rank_region = card_crop[0:rank_h, :]
        
        stats = analyse_hsv_region(rank_region, f"hero_card_{i}")
        suit = stats["detected_suit"]
        suit_name = {"h": "♥hearts", "d": "♦diamonds", "c": "♣clubs", "s": "♠spades"}.get(suit, "???")
        
        print(f"\n  Card {i} at ({x},{y}) {w}x{h}:")
        print(f"    Detected suit: {suit_name}")
        print(f"    Coloured pixels: {stats['n_coloured']} (min={stats['min_pixels']})")
        print(f"    Dark pixels: {stats['n_dark']}")
        print(f"    Red(♥)={stats['red_count']} Blue(♦)={stats['blue_count']} Green(♣)={stats['green_count']}")
        if stats.get("hue_mean") is not None:
            print(f"    Hue: mean={stats['hue_mean']:.1f} median={stats['hue_median']:.1f} std={stats['hue_std']:.1f}")
            print(f"    Sat: mean={stats['sat_mean']:.1f} median={stats['sat_median']:.1f}")
            print(f"    Val: mean={stats['val_mean']:.1f} median={stats['val_median']:.1f}")
            if stats.get("hue_histogram"):
                hist_str = " ".join(f"{k}={v}" for k, v in sorted(stats["hue_histogram"].items()))
                print(f"    Hue hist: {hist_str}")
        
        # Save individual card crops
        cv2.imwrite(str(cards_dir / f"hero_card_{i}.png"), card_crop)
        cv2.imwrite(str(cards_dir / f"hero_card_{i}_rank.png"), rank_region)
        
        # Save HSV channels
        hsv = cv2.cvtColor(rank_region, cv2.COLOR_BGR2HSV)
        cv2.imwrite(str(cards_dir / f"hero_card_{i}_hue.png"), hsv[:, :, 0])
        cv2.imwrite(str(cards_dir / f"hero_card_{i}_sat.png"), hsv[:, :, 1])
        cv2.imwrite(str(cards_dir / f"hero_card_{i}_val.png"), hsv[:, :, 2])
        
        # Draw on annotated frame
        abs_x = cr_hero_x1 + x
        abs_y = cr_hero_y1 + y
        color = {"h": (0, 0, 255), "d": (255, 0, 0), "c": (0, 128, 0), "s": (50, 50, 50)}.get(suit, (128, 128, 128))
        cv2.rectangle(annotated, (abs_x, abs_y), (abs_x + w, abs_y + h), color, 2)
        cv2.putText(annotated, f"?{suit or '?'}", (abs_x, abs_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    # ── 6. Find cards in board region ──
    print(f"\n{'='*70}")
    print("   ANÁLISE HSV — BOARD CARDS")
    print(f"{'='*70}")

    board_crop = frame[cr_board_y1:cr_board_y2, cr_board_x1:cr_board_x2]
    cv2.imwrite(str(cards_dir / "board_region.png"), board_crop)
    board_boxes = find_card_contours(board_crop)
    print(f"  Contornos de cartas encontrados: {len(board_boxes)}")

    if not board_boxes:
        gray = cv2.cvtColor(board_crop, cv2.COLOR_BGR2GRAY)
        print(f"  Board region brightness: mean={np.mean(gray):.1f} max={np.max(gray)} min={np.min(gray)}")
        _, bright100 = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
        contours100, _ = cv2.findContours(bright100, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        print(f"  Contornos com threshold=100: {len(contours100)}")
        for c in contours100:
            x, y, w, h = cv2.boundingRect(c)
            if w > 15 and h > 15:
                print(f"    bbox: ({x},{y}) {w}x{h} aspect={w/max(h,1):.2f}")
        cv2.imwrite(str(cards_dir / "board_bright140.png"),
                    cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)[1])
        cv2.imwrite(str(cards_dir / "board_bright100.png"), bright100)

    for i, (x, y, w, h) in enumerate(board_boxes):
        card_crop = board_crop[y:y+h, x:x+w]
        rank_h = max(10, int(h * 0.55))
        rank_region = card_crop[0:rank_h, :]
        
        stats = analyse_hsv_region(rank_region, f"board_card_{i}")
        suit = stats["detected_suit"]
        suit_name = {"h": "♥hearts", "d": "♦diamonds", "c": "♣clubs", "s": "♠spades"}.get(suit, "???")
        
        print(f"\n  Card {i} at ({x},{y}) {w}x{h}:")
        print(f"    Detected suit: {suit_name}")
        print(f"    Coloured pixels: {stats['n_coloured']} (min={stats['min_pixels']})")
        print(f"    Dark pixels: {stats['n_dark']}")
        print(f"    Red(♥)={stats['red_count']} Blue(♦)={stats['blue_count']} Green(♣)={stats['green_count']}")
        if stats.get("hue_mean") is not None:
            print(f"    Hue: mean={stats['hue_mean']:.1f} median={stats['hue_median']:.1f} std={stats['hue_std']:.1f}")
            print(f"    Sat: mean={stats['sat_mean']:.1f} median={stats['sat_median']:.1f}")
            print(f"    Val: mean={stats['val_mean']:.1f} median={stats['val_median']:.1f}")
            if stats.get("hue_histogram"):
                hist_str = " ".join(f"{k}={v}" for k, v in sorted(stats["hue_histogram"].items()))
                print(f"    Hue hist: {hist_str}")
        
        cv2.imwrite(str(cards_dir / f"board_card_{i}.png"), card_crop)
        cv2.imwrite(str(cards_dir / f"board_card_{i}_rank.png"), rank_region)
        
        hsv = cv2.cvtColor(rank_region, cv2.COLOR_BGR2HSV)
        cv2.imwrite(str(cards_dir / f"board_card_{i}_hue.png"), hsv[:, :, 0])
        cv2.imwrite(str(cards_dir / f"board_card_{i}_sat.png"), hsv[:, :, 1])
        cv2.imwrite(str(cards_dir / f"board_card_{i}_val.png"), hsv[:, :, 2])
        
        abs_x = cr_board_x1 + x
        abs_y = cr_board_y1 + y
        color = {"h": (0, 0, 255), "d": (255, 0, 0), "c": (0, 128, 0), "s": (50, 50, 50)}.get(suit, (128, 128, 128))
        cv2.rectangle(annotated, (abs_x, abs_y), (abs_x + w, abs_y + h), color, 2)
        cv2.putText(annotated, f"?{suit or '?'}", (abs_x, abs_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    # ── 7. Full-frame HSV analysis of known card positions ──
    print(f"\n{'='*70}")
    print("   ANÁLISE HSV — PÍXEIS DE COR DA MESA")
    print(f"{'='*70}")
    
    # Sample some known positions to understand MuMu's colour profile
    # Table felt (green area)
    felt_region = frame[400:450, 300:400]
    felt_hsv = cv2.cvtColor(felt_region, cv2.COLOR_BGR2HSV)
    print(f"  Mesa (felt): Hue={np.mean(felt_hsv[:,:,0]):.1f} Sat={np.mean(felt_hsv[:,:,1]):.1f} Val={np.mean(felt_hsv[:,:,2]):.1f}")
    
    # ── 8. Run the actual PPPokerCardReader for comparison ──
    print(f"\n{'='*70}")
    print("   RESULTADO DO PPPokerCardReader")
    print(f"{'='*70}")
    
    try:
        from tools.card_reader import PPPokerCardReader
        reader = PPPokerCardReader()
        if reader.enabled:
            action_points = {
                "fold": (126, 1220),
                "call": (361, 1220),
                "raise": (596, 1220),
            }
            hero_cards, board_cards = reader.read_cards(frame, action_points, None)
            print(f"  Hero cards:  {hero_cards}")
            print(f"  Board cards: {board_cards}")
        else:
            print("  [WARN] Card reader disabled")
    except Exception as e:
        print(f"  [ERRO] {e}")

    # ── 9. Save annotated frame ──
    out_path = str(out_dir / f"diag_suit_hsv_{ts}.png")
    cv2.imwrite(out_path, annotated)
    cv2.imwrite(str(out_dir / f"diag_frame_raw_{ts}.png"), frame)
    print(f"\n[SALVO] Frame anotado: {out_path}")
    print(f"[SALVO] Crops de cartas: {cards_dir}")

    # ── 10. Summary ──
    print(f"\n{'='*70}")
    print("   RESUMO")
    print(f"{'='*70}")
    print(f"  YOLO cartas detectadas: {len(card_dets)}")
    print(f"  Hero contornos: {len(hero_boxes)}")
    print(f"  Board contornos: {len(board_boxes)}")
    if hero_boxes:
        suits_hero = [analyse_hsv_region(
            hero_crop[y:y+h, x:x+w][0:max(10,int(h*0.55)), :], ""
        )["detected_suit"] for x, y, w, h in hero_boxes]
        print(f"  Hero suits detectados: {suits_hero}")
    if board_boxes:
        suits_board = [analyse_hsv_region(
            board_crop[y:y+h, x:x+w][0:max(10,int(h*0.55)), :], ""
        )["detected_suit"] for x, y, w, h in board_boxes]
        print(f"  Board suits detectados: {suits_board}")


if __name__ == "__main__":
    main()

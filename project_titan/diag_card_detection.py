#!/usr/bin/env python3
"""Diagnóstico de Detecção de Cartas — YOLO Live Debug.

Captura um frame do MuMu Player 12, roda o YOLO, e mostra:
  - Todas as detecções com label, confiança e posição
  - Classificação hero vs board vs desconhecido
  - Imagem anotada salva em reports/

Uso:
    cd project_titan
    python diag_card_detection.py
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

# DPI awareness ANTES de qualquer Win32
if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    k = ctypes.windll.kernel32
    k.SetConsoleMode(k.GetStdHandle(-11), 7)

# ANSI
_G = "\033[92m"; _R = "\033[91m"; _Y = "\033[93m"; _C = "\033[96m"
_B = "\033[1m"; _D = "\033[2m"; _RST = "\033[0m"


def main() -> int:
    print(f"\n{_B}{'=' * 60}")
    print(f"  DIAGNÓSTICO DE DETECÇÃO DE CARTAS — YOLO Live")
    print(f"{'=' * 60}{_RST}\n")

    # ── Imports ──
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        print(f"{_R}ERRO: {e}. Instale opencv-python e numpy.{_RST}")
        return 1

    # ── Load config for Y thresholds ──
    hero_y_min, hero_y_max = 830, 1120
    board_y_min, board_y_max = 450, 650
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from utils.titan_config import cfg
        hero_area = cfg.get_raw("vision.regions.hero_area", None)
        board_area = cfg.get_raw("vision.regions.board_area", None)
        if isinstance(hero_area, dict) and "y" in hero_area:
            y = int(hero_area["y"]); h = int(hero_area.get("h", 84))
            hero_y_min = max(0, y - 50); hero_y_max = y + h + 50
        if isinstance(board_area, dict) and "y" in board_area:
            y = int(board_area["y"]); h = int(board_area.get("h", 95))
            board_y_min = max(0, y - 50); board_y_max = y + h + 50
    except Exception:
        pass

    print(f"  {_D}Hero Y range:  [{hero_y_min}, {hero_y_max}]{_RST}")
    print(f"  {_D}Board Y range: [{board_y_min}, {board_y_max}]{_RST}")
    print()

    # ── Capture frame ──
    print(f"  {_C}Capturando frame do MuMu Player 12...{_RST}")
    try:
        from utils.emulator_profiles import get_profile, find_render_hwnd
        import ctypes.wintypes as wt
        import mss

        profile = get_profile()
        sub_hwnd = find_render_hwnd(profile)
        if not sub_hwnd:
            print(f"  {_R}HWND do render surface não encontrado!{_RST}")
            return 1

        user32 = ctypes.windll.user32

        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _POINT(0, 0)
        user32.ClientToScreen(sub_hwnd, ctypes.byref(pt))
        rect = wt.RECT()
        user32.GetClientRect(sub_hwnd, ctypes.byref(rect))
        cw, ch = rect.right, rect.bottom

        with mss.mss() as sct:
            monitor = {"left": pt.x, "top": pt.y, "width": cw, "height": ch}
            raw = np.array(sct.grab(monitor))

        frame_raw = raw[:, :, :3].copy()
        print(f"  {_G}Captura OK: {frame_raw.shape[1]}×{frame_raw.shape[0]}{_RST}")

        # Resize to 720×1280
        frame_720 = cv2.resize(frame_raw, (720, 1280), interpolation=cv2.INTER_LINEAR)
        print(f"  {_G}Resized: 720×1280{_RST}")
    except Exception as e:
        print(f"  {_R}Erro na captura: {e}{_RST}")
        import traceback; traceback.print_exc()
        return 1

    # ── Save raw frame ──
    os.makedirs("reports", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    raw_path = f"reports/diag_frame_raw_{ts}.png"
    cv2.imwrite(raw_path, frame_720)
    print(f"  {_D}Frame salvo: {raw_path}{_RST}")

    # ── Load YOLO model ──
    model_path = "models/titan_v8_pro.pt"
    if not os.path.isfile(model_path):
        # Try config
        try:
            mp = cfg.get_raw("vision.model_path", "")
            if mp:
                model_path = str(mp)
        except Exception:
            pass
    if not os.path.isfile(model_path):
        print(f"  {_R}Modelo YOLO não encontrado: {model_path}{_RST}")
        return 1

    print(f"\n  {_C}Carregando modelo: {model_path}{_RST}")
    try:
        from ultralytics import YOLO
        model = YOLO(model_path)
        class_names = model.names
        print(f"  {_G}Modelo carregado — {len(class_names)} classes{_RST}")
    except Exception as e:
        print(f"  {_R}Erro ao carregar modelo: {e}{_RST}")
        return 1

    # ── Run inference ──
    print(f"\n  {_C}Rodando inferência (conf=0.08)...{_RST}")
    t0 = time.perf_counter()
    results = model.predict(frame_720, conf=0.08, verbose=False)
    t_inf = (time.perf_counter() - t0) * 1000
    print(f"  {_G}Inferência: {t_inf:.1f}ms{_RST}")

    if not results:
        print(f"  {_R}Nenhum resultado do YOLO{_RST}")
        return 1

    result = results[0]
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        print(f"  {_Y}Nenhuma detecção.{_RST}")
        return 0

    cls_values = boxes.cls.tolist()
    xyxy_values = boxes.xyxy.tolist()
    conf_values = boxes.conf.tolist()

    # ── Classify detections ──
    SUITS = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
    SUIT_COLORS = {"c": _G, "d": "\033[94m", "h": _R, "s": _D}

    card_chars = set("23456789TJQKAcdhs")

    hero_cards = []
    board_cards = []
    action_detections = []
    other_detections = []

    print(f"\n{_B}{'─' * 60}")
    print(f"  TODAS AS DETECÇÕES ({len(cls_values)} total)")
    print(f"{'─' * 60}{_RST}")
    print(f"  {'Label':<12} {'Conf':>6}  {'CenterX':>7}  {'CenterY':>7}  {'Zone':<10}")
    print(f"  {'─'*12} {'─'*6}  {'─'*7}  {'─'*7}  {'─'*10}")

    annotated = frame_720.copy()

    for idx, (cls_idx, xyxy, conf) in enumerate(zip(cls_values, xyxy_values, conf_values)):
        label = class_names.get(int(cls_idx), f"?{int(cls_idx)}")
        cx = (xyxy[0] + xyxy[2]) / 2
        cy = (xyxy[1] + xyxy[3]) / 2
        x1, y1, x2, y2 = int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])

        # Is it a card?
        is_card = len(label) == 2 and label[0] in "23456789TJQKA" and label[1] in "cdhs"

        zone = ""
        color = _D
        box_color = (128, 128, 128)

        if is_card:
            suit_char = SUITS.get(label[1], "?")
            display = f"{label[0]}{suit_char}"
            s_col = SUIT_COLORS.get(label[1], "")

            if hero_y_min <= cy <= hero_y_max:
                zone = "HERO"
                color = _G
                box_color = (0, 255, 0)
                hero_cards.append((label, conf, cx, cy))
            elif board_y_min <= cy <= board_y_max:
                zone = "BOARD"
                color = _C
                box_color = (255, 255, 0)
                board_cards.append((label, conf, cx, cy))
            else:
                zone = f"Y={cy:.0f} ???"
                color = _Y
                box_color = (0, 165, 255)

            print(f"  {s_col}{display:<12}{_RST} {conf:>6.3f}  {cx:>7.1f}  {cy:>7.1f}  {color}{zone}{_RST}")
        else:
            # Action / UI
            if label in {"fold", "check", "raise", "raise_2x", "raise_2_5x",
                         "raise_pot", "raise_confirm", "allin"}:
                zone = "ACTION"
                color = "\033[95m"
                box_color = (255, 0, 255)
                action_detections.append((label, conf, cx, cy))
            elif label in {"pot", "stack"}:
                zone = "UI"
                color = _D
                box_color = (200, 200, 200)
                other_detections.append((label, conf, cx, cy))
            else:
                zone = "OTHER"
                other_detections.append((label, conf, cx, cy))

            print(f"  {label:<12} {conf:>6.3f}  {cx:>7.1f}  {cy:>7.1f}  {color}{zone}{_RST}")

        # Draw on annotated image
        cv2.rectangle(annotated, (x1, y1), (x2, y2), box_color, 2)
        txt = f"{label} {conf:.2f}"
        cv2.putText(annotated, txt, (x1, max(y1 - 5, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, box_color, 1)

    # Draw zone lines
    cv2.line(annotated, (0, hero_y_min), (720, hero_y_min), (0, 255, 0), 1)
    cv2.line(annotated, (0, hero_y_max), (720, hero_y_max), (0, 255, 0), 1)
    cv2.putText(annotated, "HERO Y MIN", (5, hero_y_min - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
    cv2.putText(annotated, "HERO Y MAX", (5, hero_y_max + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
    cv2.line(annotated, (0, board_y_min), (720, board_y_min), (255, 255, 0), 1)
    cv2.line(annotated, (0, board_y_max), (720, board_y_max), (255, 255, 0), 1)
    cv2.putText(annotated, "BOARD Y MIN", (5, board_y_min - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
    cv2.putText(annotated, "BOARD Y MAX", (5, board_y_max + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

    # Save annotated frame
    ann_path = f"reports/diag_frame_annotated_{ts}.png"
    cv2.imwrite(ann_path, annotated)
    print(f"\n  {_D}Annotated frame: {ann_path}{_RST}")

    # ── Summary ──
    print(f"\n{_B}{'─' * 60}")
    print(f"  RESUMO")
    print(f"{'─' * 60}{_RST}")

    if board_cards:
        board_str = " ".join(f"{l[0]}{SUITS.get(l[1],'?')}" for l, *_ in board_cards)
        print(f"  {_C}BOARD detectado: {board_str}{_RST}")
        for label, conf, cx, cy in board_cards:
            suit_full = {"c": "Clubs ♣", "d": "Diamonds ♦", "h": "Hearts ♥", "s": "Spades ♠"}
            print(f"    {label[0]}{SUITS[label[1]]}  conf={conf:.3f}  pos=({cx:.0f}, {cy:.0f})  suit={suit_full.get(label[1], '?')}")
    else:
        print(f"  {_R}BOARD: nenhuma carta detectada na zona Y [{board_y_min}, {board_y_max}]{_RST}")

    if hero_cards:
        hero_str = " ".join(f"{l[0]}{SUITS.get(l[1],'?')}" for l, *_ in hero_cards)
        print(f"  {_G}HERO detectado:  {hero_str}{_RST}")
        for label, conf, cx, cy in hero_cards:
            suit_full = {"c": "Clubs ♣", "d": "Diamonds ♦", "h": "Hearts ♥", "s": "Spades ♠"}
            print(f"    {label[0]}{SUITS[label[1]]}  conf={conf:.3f}  pos=({cx:.0f}, {cy:.0f})  suit={suit_full.get(label[1], '?')}")
    else:
        print(f"  {_R}HERO: nenhuma carta detectada na zona Y [{hero_y_min}, {hero_y_max}]{_RST}")
        # Check if cards exist outside zones
        all_card_detections = [(l, c, x, y) for l, c, x, y in
                               [(class_names.get(int(ci), ""), co, (xy[0]+xy[2])/2, (xy[1]+xy[3])/2)
                                for ci, xy, co in zip(cls_values, xyxy_values, conf_values)]
                               if len(l) == 2 and l[0] in "23456789TJQKA" and l[1] in "cdhs"
                               and not (board_y_min <= y <= board_y_max)]
        orphans = [(l, c, x, y) for l, c, x, y in all_card_detections
                   if not (hero_y_min <= y <= hero_y_max)]
        if orphans:
            print(f"\n  {_Y}CARTAS FORA DAS ZONAS (possíveis hero cards):{_RST}")
            for label, conf, cx, cy in orphans:
                print(f"    {label[0]}{SUITS[label[1]]}  conf={conf:.3f}  pos=({cx:.0f}, {cy:.0f})  "
                      f"{'ABAIXO hero' if cy > hero_y_max else 'ACIMA hero' if cy < hero_y_min else '???'}")

    if action_detections:
        acts = ", ".join(f"{l}({c:.2f})" for l, c, *_ in action_detections)
        print(f"  {_B}AÇÕES: {acts}{_RST}")

    # ── Suit confusion analysis ──
    print(f"\n{_B}{'─' * 60}")
    print(f"  ANÁLISE DE CONFUSÃO DE NAIPES")
    print(f"{'─' * 60}{_RST}")
    print(f"  {_D}Se o modelo confunde ♥ com ♠, as detecções de 4h vs 4s")
    print(f"  terão confidence semelhante. Compare abaixo:{_RST}")
    for label, conf, cx, cy in board_cards + hero_cards:
        print(f"    {label}  (class idx={list(class_names.values()).index(label) if label in class_names.values() else '?'})  "
              f"conf={conf:.4f}  → modelo diz: {label[0]} de {SUITS.get(label[1], '?')}")

    print(f"\n  {_D}Modelo: {model_path}{_RST}")
    print(f"  {_D}Total detecções: {len(cls_values)}{_RST}")
    print(f"  {_D}Inference: {t_inf:.1f}ms{_RST}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

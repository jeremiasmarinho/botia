"""
Project Titan — Live Demo
==========================

Captura a tela do emulador em tempo real, roda YOLO e mostra as
detecções com bounding boxes coloridas numa janela OpenCV.

Uso:
    python scripts/live_demo.py
    python scripts/live_demo.py --model "C:/botia/runs/detect/runs/detect/titan_v5_synth_nano/weights/best.pt"

Controles:
    Q / ESC  → Sair
    S        → Salvar screenshot com detecções
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np


# ── Cores por categoria ───────────────────────────────────────────
CARD_SUITS = {"c": (0, 180, 0), "d": (0, 100, 255), "h": (0, 0, 255), "s": (180, 180, 180)}
BUTTON_COLOR = (0, 165, 255)   # laranja
POT_COLOR = (0, 255, 255)      # amarelo
STACK_COLOR = (255, 200, 0)    # cyan

def get_color(label: str) -> tuple:
    """Retorna cor BGR baseada no tipo de detecção."""
    label_lower = label.lower()
    if label_lower.startswith("btn_") or label_lower in ("fold", "call", "raise_small", "raise_big"):
        return BUTTON_COLOR
    if label_lower == "pot":
        return POT_COLOR
    if label_lower == "stack":
        return STACK_COLOR
    # Carta — cor pelo naipe (último caractere)
    if len(label) >= 2 and label[-1] in CARD_SUITS:
        return CARD_SUITS[label[-1]]
    return (200, 200, 200)


def draw_detections(frame: np.ndarray, results, names: dict) -> np.ndarray:
    """Desenha bounding boxes e labels no frame."""
    overlay = frame.copy()
    boxes = results[0].boxes if results else []

    card_list = []

    for box in boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        label = names.get(cls_id, f"cls_{cls_id}")
        color = get_color(label)

        # Caixa semi-transparente
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
        cv2.addWeighted(overlay, 0.25, frame, 0.75, 0, frame)
        overlay = frame.copy()

        # Borda
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        # Label com background
        text = f"{label} {conf:.0%}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
        cv2.putText(frame, text, (x1 + 3, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

        # Coletar cartas para o HUD
        if len(label) == 2 and label[-1] in "cdhs":
            card_list.append((label, conf))

    return frame, card_list


def draw_hud(frame: np.ndarray, card_list: list, fps: float, inference_ms: float) -> np.ndarray:
    """Desenha HUD informativo no canto superior."""
    h, w = frame.shape[:2]
    panel_w = min(300, w - 20)
    if panel_w < 100:
        return frame

    # Painel semi-transparente
    panel_h = 100 + len(card_list) * 22
    panel_h = min(panel_h, h - 20)
    x1_p = w - panel_w - 10
    y1_p = 10
    y2_p = y1_p + panel_h
    x2_p = w - 10
    overlay = frame.copy()
    cv2.rectangle(overlay, (x1_p, y1_p), (x2_p, y2_p), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

    tx = x1_p + 10  # margem interna do painel
    y = 35
    cv2.putText(frame, "PROJECT TITAN - LIVE", (tx, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    y += 25
    cv2.putText(frame, f"FPS: {fps:.1f}  |  YOLO: {inference_ms:.0f}ms", (tx, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
    y += 25
    cv2.putText(frame, f"Cartas detectadas: {len(card_list)}", (tx, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

    # Listar cartas
    for card_name, conf in card_list:
        y += 22
        if y > 10 + panel_h - 5:
            break
        suit_char = card_name[-1]
        suit_names = {"c": "Clubs", "d": "Diamonds", "h": "Hearts", "s": "Spades"}
        rank_names = {
            "2": "2", "3": "3", "4": "4", "5": "5", "6": "6",
            "7": "7", "8": "8", "9": "9", "T": "10",
            "J": "J", "Q": "Q", "K": "K", "A": "A",
        }
        rank = rank_names.get(card_name[0], card_name[0])
        suit = suit_names.get(suit_char, suit_char)
        color = CARD_SUITS.get(suit_char, (200, 200, 200))
        cv2.putText(frame, f"  {rank} of {suit} ({conf:.0%})", (tx, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)

    return frame


def main():
    parser = argparse.ArgumentParser(description="Project Titan — Live Vision Demo")
    parser.add_argument("--model", type=str, default="",
                        help="Caminho do modelo YOLO .pt")
    parser.add_argument("--title", type=str, default="LDPlayer",
                        help="Título da janela do emulador")
    parser.add_argument("--conf", type=float, default=0.25,
                        help="Confiança mínima YOLO")
    args = parser.parse_args()

    # ── Localizar modelo ──
    model_path = args.model
    if not model_path:
        # Tentar o melhor modelo disponível
        candidates = [
            PROJECT_ROOT.parent / "runs" / "detect" / "runs" / "detect" / "titan_v5_synth_nano" / "weights" / "best.pt",
            PROJECT_ROOT / "models" / "titan_v1.pt",
        ]
        for c in candidates:
            if c.exists():
                model_path = str(c)
                break

    if not model_path or not Path(model_path).exists():
        print(f"[ERRO] Modelo YOLO não encontrado. Use --model <caminho>")
        sys.exit(1)

    print(f"[INFO] Modelo: {model_path}")

    # ── Carregar YOLO ──
    from ultralytics import YOLO
    model = YOLO(model_path)
    names = model.names
    print(f"[INFO] Classes: {len(names)}")

    # ── Localizar emulador ──
    try:
        import win32gui
    except ImportError:
        print("[ERRO] pywin32 não instalado. Execute: pip install pywin32")
        sys.exit(1)

    import mss

    def find_window(title_pattern: str) -> tuple:
        """Encontra a janela do emulador."""
        result = []
        def callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title_pattern.lower() in title.lower():
                    rect = win32gui.GetWindowRect(hwnd)
                    result.append((hwnd, title, rect))
        win32gui.EnumWindows(callback, None)
        return result

    windows = find_window(args.title)
    if not windows:
        print(f"[ERRO] Janela '{args.title}' não encontrada.")
        print(f"[INFO] Janelas visíveis:")
        def list_windows(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title.strip():
                    print(f"  - {title}")
        win32gui.EnumWindows(list_windows, None)
        sys.exit(1)

    hwnd, win_title, rect = windows[0]
    print(f"[INFO] Emulador: {win_title}")
    print(f"[INFO] Posição: {rect}")

    # Chrome do LDPlayer
    chrome_top = 35
    chrome_right = 38

    # ── Loop principal ──
    print()
    print("=" * 50)
    print("  TITAN LIVE DEMO - Pressione Q para sair")
    print("  S para salvar screenshot")
    print("=" * 50)
    print()

    sct = mss.mss()
    fps = 0.0
    frame_count = 0
    fps_start = time.perf_counter()
    screenshot_count = 0

    while True:
        # Atualizar posição da janela (pode ter movido)
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            print("[WARN] Janela perdida, tentando reconectar...")
            time.sleep(1)
            windows = find_window(args.title)
            if windows:
                hwnd, win_title, rect = windows[0]
            continue

        # Região de captura (sem chrome)
        monitor = {
            "left": rect[0],
            "top": rect[1] + chrome_top,
            "width": rect[2] - rect[0] - chrome_right,
            "height": rect[3] - rect[1] - chrome_top,
        }

        if monitor["width"] < 100 or monitor["height"] < 100:
            time.sleep(0.1)
            continue

        # Capturar tela
        screenshot = sct.grab(monitor)
        frame = np.ascontiguousarray(np.array(screenshot)[:, :, :3])  # BGRA → BGR contíguo

        # Inferência YOLO
        t0 = time.perf_counter()
        results = model.predict(frame, conf=args.conf, verbose=False)
        inference_ms = (time.perf_counter() - t0) * 1000

        # Desenhar detecções
        frame, card_list = draw_detections(frame, results, names)

        # Calcular FPS
        frame_count += 1
        elapsed = time.perf_counter() - fps_start
        if elapsed >= 1.0:
            fps = frame_count / elapsed
            frame_count = 0
            fps_start = time.perf_counter()

        # Desenhar HUD
        frame = draw_hud(frame, card_list, fps, inference_ms)

        # Mostrar
        cv2.imshow("Project Titan - Live Demo", frame)

        # Controles
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), ord("Q"), 27):  # Q ou ESC
            break
        elif key in (ord("s"), ord("S")):
            screenshot_count += 1
            fname = f"titan_screenshot_{screenshot_count:03d}.png"
            cv2.imwrite(str(PROJECT_ROOT / "reports" / fname), frame)
            print(f"[SAVE] Screenshot salva: reports/{fname}")

    cv2.destroyAllWindows()
    print("\n[INFO] Demo encerrada.")


if __name__ == "__main__":
    main()

"""Debug OCR Regions — Captura screenshot e mostra onde cada região OCR aponta.

Salva a imagem com retângulos coloridos sobre as regiões do OCR para
ajustar as coordenadas em config_club.yaml.

Uso:
    python debug_ocr_regions.py
"""

from __future__ import annotations

import os
import sys
import time

import cv2
import numpy as np
import yaml

# ── Importar VisionYolo para capturar via mss ─────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.vision_yolo import VisionYolo


def load_ocr_regions(config_path: str = "config_club.yaml") -> dict:
    """Load OCR region config."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    ocr = cfg.get("ocr", {})
    regions = {}
    for key in ("pot_region", "stack_region", "call_region"):
        val = ocr.get(key, "")
        if val:
            parts = [int(p.strip()) for p in str(val).split(",")]
            if len(parts) == 4:
                name = key.replace("_region", "")
                if name == "stack":
                    name = "hero_stack"
                if name == "call":
                    name = "call_amount"
                regions[name] = tuple(parts)
    return regions


def main() -> None:
    # Capture frame
    print("Capturando screenshot do LDPlayer...")
    vision = VisionYolo()
    if not vision.find_window():
        print("LDPlayer não encontrado!")
        return

    frame = vision.capture_frame()
    if frame is None:
        print("Falha na captura!")
        return

    h, w = frame.shape[:2]
    print(f"Frame capturado: {w}x{h}")

    # Load OCR regions (Android 720x1280 coords)
    regions = load_ocr_regions()
    ref_w, ref_h = 720, 1280
    sx = w / ref_w
    sy = h / ref_h
    print(f"Escala: sx={sx:.4f}, sy={sy:.4f}")

    # Define colors for each region
    colors = {
        "pot": (0, 255, 0),         # Green
        "hero_stack": (0, 0, 255),   # Red
        "call_amount": (255, 0, 0),  # Blue
    }
    labels = {
        "pot": "POT",
        "hero_stack": "STACK",
        "call_amount": "CALL",
    }

    annotated = frame.copy()

    # Draw regions
    for name, (rx, ry, rw, rh) in regions.items():
        # Scale from Android coords to canvas coords
        cx = int(rx * sx)
        cy = int(ry * sy)
        cw = int(rw * sx)
        ch = int(rh * sy)

        color = colors.get(name, (255, 255, 255))
        label = labels.get(name, name)

        # Draw rectangle
        cv2.rectangle(annotated, (cx, cy), (cx + cw, cy + ch), color, 2)

        # Draw label
        cv2.putText(
            annotated, f"{label} ({rx},{ry},{rw},{rh})",
            (cx, cy - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
        )

        # Crop region and try OCR
        crop = frame[cy : cy + ch, cx : cx + cw]
        if crop.size > 0:
            crop_path = f"reports/ocr_crop_{name}.png"
            cv2.imwrite(crop_path, crop)
            print(f"  {label}: android=({rx},{ry},{rw},{rh}) → canvas=({cx},{cy},{cw},{ch})")

            # Try Tesseract OCR on the crop
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = (
                    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
                )
                # Preprocess: grayscale + threshold
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                text = pytesseract.image_to_string(
                    thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789.,"
                ).strip()
                print(f"         OCR leu: '{text}'")
            except Exception as e:
                print(f"         OCR erro: {e}")

    # Save annotated image
    os.makedirs("reports", exist_ok=True)
    out_path = "reports/ocr_regions_debug.png"
    cv2.imwrite(out_path, annotated)
    print(f"\nImagem salva: {out_path}")

    # Also save clean screenshot
    clean_path = "reports/screenshot_clean.png"
    cv2.imwrite(clean_path, frame)
    print(f"Screenshot limpo: {clean_path}")

    # ── Suggest correct regions ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("ANÁLISE DE REGIÕES")
    print("=" * 60)
    print(f"Frame (canvas): {w}x{h}")
    print(f"Referência (Android): {ref_w}x{ref_h}")
    print()

    # Interactive: show key areas
    # Find text-like areas in the bottom portion (where stack/call are)
    bottom = frame[int(h * 0.8):, :]
    gray_bottom = cv2.cvtColor(bottom, cv2.COLOR_BGR2GRAY)
    _, thresh_bottom = cv2.threshold(gray_bottom, 200, 255, cv2.THRESH_BINARY)
    cv2.imwrite("reports/ocr_bottom_thresh.png", thresh_bottom)
    print("Área inferior (80-100%): reports/ocr_bottom_thresh.png")

    # Suggest based on typical PPPoker layout
    print()
    print("SUGESTÃO DE REGIÕES (baseado no layout PPPoker PLO):")
    print("  Para ajustar, analise os arquivos em reports/ e corrija config_club.yaml")
    print()
    print(f"  pot_region:    {regions.get('pot', 'N/A')} → parece OK (pot lê corretamente)")

    # The stack area: scan for bright text near bottom
    # "74" should be white text on dark background
    scan_y_start = int(h * 0.85)
    scan_y_end = int(h * 0.95)
    scan_area = frame[scan_y_start:scan_y_end, :]
    cv2.imwrite("reports/ocr_stack_scan.png", scan_area)
    print(f"  Área de scan (85-95%): reports/ocr_stack_scan.png")
    print(f"    Canvas y={scan_y_start}-{scan_y_end} → Android y={int(scan_y_start/sy)}-{int(scan_y_end/sy)}")

    scan_btn_start = int(h * 0.94)
    scan_btn_end = h
    btn_area = frame[scan_btn_start:scan_btn_end, :]
    cv2.imwrite("reports/ocr_button_scan.png", btn_area)
    print(f"  Área de botões (94-100%): reports/ocr_button_scan.png")
    print(f"    Canvas y={scan_btn_start}-{scan_btn_end} → Android y={int(scan_btn_start/sy)}-{int(scan_btn_end/sy)}")


if __name__ == "__main__":
    main()

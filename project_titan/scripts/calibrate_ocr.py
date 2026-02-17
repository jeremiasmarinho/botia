"""OCR calibration utility for Project Titan.

Captura um frame do emulador, desenha as regiões de OCR e imprime
os valores lidos para facilitar calibração de `pot`, `hero_stack` e
`call_amount` antes de produção.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate OCR ROIs on emulator frame")
    parser.add_argument("--emulator", type=str, default="", help="Partial emulator title (default env TITAN_EMULATOR_TITLE)")
    parser.add_argument("--save", type=str, default="", help="Output image path")
    parser.add_argument("--show", action="store_true", help="Display preview window")
    parser.add_argument("--interval", type=float, default=0.0, help="Refresh interval for continuous mode (0 = single capture)")
    return parser.parse_args()


def _draw_overlay(frame, regions: dict[str, tuple[int, int, int, int]], values: dict[str, float]):
    import cv2  # type: ignore[import-untyped]

    canvas = frame.copy()
    color_map = {
        "pot": (0, 255, 255),
        "hero_stack": (0, 255, 0),
        "call_amount": (255, 255, 0),
    }

    for key, (x, y, w, h) in regions.items():
        color = color_map.get(key, (255, 255, 255))
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)
        text = f"{key}: {values.get(key, 0.0):.2f}"
        cv2.putText(canvas, text, (x, max(20, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    return canvas


def main() -> int:
    args = _parse_args()

    try:
        from agent.vision_ocr import TitanOCR
        from agent.vision_yolo import VisionYolo
        from utils.config import OCRRuntimeConfig
    except Exception as error:
        print(f"[calibrate_ocr] import error: {error}")
        return 1

    if args.emulator.strip():
        os.environ["TITAN_EMULATOR_TITLE"] = args.emulator.strip()

    config = OCRRuntimeConfig()
    ocr = TitanOCR(use_easyocr=config.use_easyocr, tesseract_cmd=config.tesseract_cmd or None)
    vision = VisionYolo(model_path="")

    if not vision.find_window():
        print("[calibrate_ocr] emulator not found")
        return 1

    print(f"[calibrate_ocr] emulator={vision.emulator!r}")
    print(f"[calibrate_ocr] regions={config.regions()}")

    save_path = args.save.strip()
    if not save_path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        save_path = str(PROJECT_ROOT / "reports" / f"ocr_calibration_{timestamp}.png")

    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    interval = max(0.0, float(args.interval))

    while True:
        frame = vision.capture_frame()
        if frame is None:
            print("[calibrate_ocr] failed to capture frame")
            return 1

        regions = config.regions()
        values: dict[str, float] = {}
        for key, (x, y, w, h) in regions.items():
            crop = frame[y:y + h, x:x + w]
            values[key] = ocr.read_numeric_region(crop, key=key, fallback=0.0)

        print(
            "[calibrate_ocr] "
            f"pot={values.get('pot', 0.0):.2f} "
            f"stack={values.get('hero_stack', 0.0):.2f} "
            f"call={values.get('call_amount', 0.0):.2f}"
        )

        try:
            import cv2  # type: ignore[import-untyped]

            overlay = _draw_overlay(frame, regions, values)
            cv2.imwrite(save_path, overlay)
            if args.show:
                cv2.imshow("Titan OCR Calibration", overlay)
                key = cv2.waitKey(1) & 0xFF
                if key in {27, ord('q')}:
                    break
        except Exception as error:
            print(f"[calibrate_ocr] preview/write error: {error}")
            return 1

        if interval <= 0:
            break
        time.sleep(interval)

    print(f"[calibrate_ocr] saved={save_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

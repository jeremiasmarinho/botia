"""Capture fresh card templates from a live PPPoker game via MuMu.

This script captures a screenshot from the MuMu emulator, lets the user
identify card regions, and saves individual card crops as template images
for the TemplateCardReader.

Usage
-----
    python scripts/capture_templates.py

The script will:
1. Capture a screenshot from MuMu.
2. Display it and let you select the hero/board regions.
3. Auto-detect card-shaped bright regions via contour detection.
4. Show each detected crop and ask you to label it (e.g. "Ah", "Kd").
5. Save labelled crops to ``assets/cards/`` (overwrites existing).

Alternatively, run in batch mode to re-capture from a saved screenshot::

    python scripts/capture_templates.py --from-file screenshot.png

Requirements
~~~~~~~~~~~~
- ``cv2``, ``numpy``, ``mss``
- MuMu Player 12 running with PPPoker open
"""

from __future__ import annotations

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import cv2
import numpy as np


def capture_mumu_screenshot() -> np.ndarray | None:
    """Capture a screenshot from the MuMu Player window."""
    try:
        from agent.vision_yolo import VisionYolo
        vision = VisionYolo.__new__(VisionYolo)
        # Minimal init to capture frame
        vision._hwnd = None
        vision._child_hwnd = None
        vision._model = None
        # Try to find MuMu window
        import ctypes
        user32 = ctypes.windll.user32

        def find_mumu(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if "MuMu" in title or "mumu" in title.lower():
                    find_mumu.hwnd = hwnd
            return True
        find_mumu.hwnd = None
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        user32.EnumWindows(WNDENUMPROC(find_mumu), 0)

        if find_mumu.hwnd:
            import mss
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(find_mumu.hwnd, ctypes.byref(rect))
            monitor = {
                "top": rect.top,
                "left": rect.left,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            }
            with mss.mss() as sct:
                img = sct.grab(monitor)
                return np.array(img)[:, :, :3]
    except Exception as e:
        print(f"Could not capture MuMu: {e}")

    # Fallback: capture full screen
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            return np.array(img)[:, :, :3]
    except Exception as e:
        print(f"Could not capture screen: {e}")
        return None


def find_card_bboxes(
    region: np.ndarray,
    threshold: int = 140,
) -> list[tuple[int, int, int, int]]:
    """Find card-shaped bounding boxes in a region via brightness contours."""
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    bboxes = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if 25 < w < 120 and 35 < h < 150:
            aspect = w / max(h, 1)
            if 0.3 < aspect < 1.3:
                bboxes.append((x, y, w, h))
        elif w > 120 and h > 35:
            # Probably multiple merged cards
            n = max(2, round(w / 50))
            cw = w // n
            for i in range(n):
                cx = x + i * cw
                bboxes.append((cx, y, cw, h))

    bboxes.sort(key=lambda b: b[0])
    return bboxes


def interactive_label(
    region: np.ndarray,
    bboxes: list[tuple[int, int, int, int]],
    output_dir: str,
) -> int:
    """Show each card crop and ask user to label it."""
    saved = 0
    valid_ranks = set("A23456789TJQK")
    valid_suits = set("hsdc")

    for i, (x, y, w, h) in enumerate(bboxes):
        crop = region[y : y + h, x : x + w]
        # Show the crop
        display = cv2.resize(crop, (w * 4, h * 4), interpolation=cv2.INTER_NEAREST)
        cv2.imshow(f"Card {i+1}/{len(bboxes)}", display)
        cv2.waitKey(200)

        label = input(
            f"  Card {i+1}/{len(bboxes)} at ({x},{y},{w},{h}) — "
            f"Enter token (e.g. Ah, Kd) or 's' to skip, 'q' to quit: "
        ).strip()

        cv2.destroyAllWindows()

        if label.lower() == "q":
            break
        if label.lower() == "s" or not label:
            continue
        if len(label) != 2 or label[0] not in valid_ranks or label[1] not in valid_suits:
            print(f"  Invalid token '{label}', skipping")
            continue

        path = os.path.join(output_dir, f"{label}.png")
        cv2.imwrite(path, crop)
        print(f"  Saved → {path}")
        saved += 1

    return saved


def auto_label_from_templates(
    region: np.ndarray,
    bboxes: list[tuple[int, int, int, int]],
    template_dir: str,
) -> list[tuple[str, float]]:
    """Auto-label detected crops by matching against existing templates."""
    from tools.template_card_reader import TemplateCardReader

    reader = TemplateCardReader(template_dir=template_dir)
    results = []

    for x, y, w, h in bboxes:
        crop = region[y : y + h, x : x + w]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(
            gray,
            (reader.CANONICAL_W, reader.CANONICAL_H),
            interpolation=cv2.INTER_AREA,
        )

        best_token = "??"
        best_diff = float("inf")
        for token, tmpl in reader._templates.items():
            diff = cv2.absdiff(resized, tmpl)
            score = float(np.sum(diff)) / 255.0 / (reader.CANONICAL_W * reader.CANONICAL_H)
            if score < best_diff:
                best_diff = score
                best_token = token

        confidence = 1.0 - best_diff
        results.append((best_token, confidence))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture card templates from PPPoker")
    parser.add_argument("--from-file", type=str, help="Use a saved screenshot instead of live capture")
    parser.add_argument("--output", type=str, default=os.path.join(PROJECT_DIR, "assets", "cards"), help="Output directory")
    parser.add_argument("--auto", action="store_true", help="Auto-label using existing templates (no interaction)")
    parser.add_argument("--region", type=str, default="hero", choices=["hero", "board", "full"], help="Which region to scan")
    parser.add_argument("--threshold", type=int, default=140, help="Brightness threshold for card detection")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # Get the frame
    if args.from_file:
        frame = cv2.imread(args.from_file)
        if frame is None:
            print(f"Error: Could not read {args.from_file}")
            return
    else:
        print("Capturing screenshot from MuMu...")
        frame = capture_mumu_screenshot()
        if frame is None:
            print("Error: Could not capture screenshot")
            return

    h, w = frame.shape[:2]
    print(f"Frame: {w}×{h}")

    # Determine region to scan
    if args.region == "full":
        region = frame
    elif args.region == "hero":
        # Hero region for 720×1280
        y1 = max(0, int(h * 0.62))
        y2 = min(h, int(h * 0.88))
        x1 = max(0, int(w * 0.10))
        x2 = min(w, int(w * 0.90))
        region = frame[y1:y2, x1:x2]
        print(f"Hero region: x=[{x1},{x2}], y=[{y1},{y2}]")
    else:
        # Board region
        y1 = max(0, int(h * 0.33))
        y2 = min(h, int(h * 0.52))
        x1 = max(0, int(w * 0.10))
        x2 = min(w, int(w * 0.90))
        region = frame[y1:y2, x1:x2]
        print(f"Board region: x=[{x1},{x2}], y=[{y1},{y2}]")

    # Find card bboxes
    bboxes = find_card_bboxes(region, args.threshold)
    print(f"Found {len(bboxes)} card-shaped regions")

    if not bboxes:
        print("No cards detected. Try adjusting --threshold or --region.")
        return

    if args.auto:
        # Auto-label mode
        results = auto_label_from_templates(region, bboxes, args.output)
        for (x, y, w, h), (token, conf) in zip(bboxes, results):
            print(f"  ({x},{y},{w},{h}) → {token} (conf={conf:.3f})")
    else:
        # Interactive mode
        saved = interactive_label(region, bboxes, args.output)
        print(f"\nDone. Saved {saved} template(s) to {args.output}")


if __name__ == "__main__":
    main()

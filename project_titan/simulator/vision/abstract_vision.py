from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from typing import Any


CARD_PATTERN = re.compile(r"([2-9TJQKA])([CDHScdhs])")


@dataclass(slots=True)
class WindowRegion:
    left: int
    top: int
    width: int
    height: int


@dataclass(slots=True)
class DetectionResult:
    cards: list[str]
    labels: list[str]


def find_window_region(window_title: str) -> WindowRegion:
    import pygetwindow as gw

    matches = [window for window in gw.getWindowsWithTitle(window_title) if window.width > 0 and window.height > 0]
    if not matches:
        raise RuntimeError(f"Window with title containing '{window_title}' was not found")

    window = matches[0]
    return WindowRegion(left=window.left, top=window.top, width=window.width, height=window.height)


def capture_window(region: WindowRegion) -> Any:
    import mss
    import numpy as np

    monitor = {
        "left": int(region.left),
        "top": int(region.top),
        "width": int(region.width),
        "height": int(region.height),
    }

    with mss.mss() as sct:
        frame = np.array(sct.grab(monitor))
    return frame[:, :, :3]


def normalize_card_label(label: str) -> str | None:
    cleaned = label.replace("10", "T").replace("_", "").replace("-", "")
    match = CARD_PATTERN.search(cleaned)
    if match is None:
        return None

    rank = match.group(1).upper()
    suit = match.group(2).lower()
    return f"{rank}{suit}"


def detect_cards(frame: Any, model: Any) -> DetectionResult:
    results = model.predict(source=frame, verbose=False)
    if not results:
        return DetectionResult(cards=[], labels=[])

    result = results[0]
    names: dict[int, str] = getattr(result, "names", {})
    boxes = getattr(result, "boxes", None)
    if boxes is None or boxes.cls is None:
        return DetectionResult(cards=[], labels=[])

    labels: list[str] = []
    cards: list[str] = []

    for cls_idx in boxes.cls.tolist():
        label = names.get(int(cls_idx), "")
        labels.append(label)
        card = normalize_card_label(label)
        if card is not None and card not in cards:
            cards.append(card)

    return DetectionResult(cards=cards, labels=labels)


def run_loop(window_title: str, model_path: str, interval_seconds: float) -> None:
    from ultralytics import YOLO

    model = YOLO(model_path)
    print(f"[Vision] Model loaded: {model_path}")

    while True:
        region = find_window_region(window_title)
        frame = capture_window(region)
        detection = detect_cards(frame, model)

        payload = {
            "window_title": window_title,
            "region": region.__dict__,
            "cards": detection.cards,
            "labels": detection.labels,
            "timestamp": time.time(),
        }
        print(json.dumps(payload, ensure_ascii=False))
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Abstract Vision - capture generic Windows app and detect cards with YOLO")
    parser.add_argument("--window-title", required=True, help="Partial window title to capture")
    parser.add_argument("--model", required=True, help="Path to YOLO model (.pt)")
    parser.add_argument("--interval", type=float, default=1.0, help="Capture interval in seconds")
    args = parser.parse_args()

    run_loop(window_title=args.window_title, model_path=args.model, interval_seconds=args.interval)


if __name__ == "__main__":
    main()

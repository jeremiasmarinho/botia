"""
Project Titan — PPPoker-Realistic Synthetic Data Generator v3
=============================================================

Generates realistic synthetic training images that match the PPPoker PLO6
visual style, specifically addressing hero card detection (gold borders)
and opponent showdown card detection.

Key improvements over generate_synthetic_data.py:
  1. **Gold border rendering** on hero cards (PPPoker hero card style)
  2. **Realistic showdown occlusion** — cards at 50-70% scale, overlapping
     40-60% (PLO5/PLO6), perspective-warped per seat position, with
     smart bounding boxes on visible portion only (<20% visible → discard)
  3. **PPPoker-specific layouts** — hero cards at bottom ~80% Y,
     board cards at center ~40% Y, opponent cards at periphery
  4. **Button/UI element rendering** — fold/check/raise buttons at
     bottom of screen (for full 62-class model training)
  5. **Domain Randomization** — motion blur, JPEG artifacts, glow/bloom,
     gamma shift, noise, partial occlusion, crop jitter
  6. **Perspective warping** — cards at table edges get camera-angle
     distortion matching real emulator screenshots

Usage:
    python training/generate_pppoker_data.py
    python training/generate_pppoker_data.py --num-images 5000 --output datasets/synthetic_v3
    python training/generate_pppoker_data.py --gold-border --showdown --buttons

Requisitos:
    pip install opencv-python numpy tqdm
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
from tqdm import tqdm

# ──────────────────────────────────────────────
# Class map (identical to data.yaml — full 62 classes)
# ──────────────────────────────────────────────
CARD_CLASS_MAP: dict[str, int] = {
    "2c": 0,  "2d": 1,  "2h": 2,  "2s": 3,
    "3c": 4,  "3d": 5,  "3h": 6,  "3s": 7,
    "4c": 8,  "4d": 9,  "4h": 10, "4s": 11,
    "5c": 12, "5d": 13, "5h": 14, "5s": 15,
    "6c": 16, "6d": 17, "6h": 18, "6s": 19,
    "7c": 20, "7d": 21, "7h": 22, "7s": 23,
    "8c": 24, "8d": 25, "8h": 26, "8s": 27,
    "9c": 28, "9d": 29, "9h": 30, "9s": 31,
    "Tc": 32, "Td": 33, "Th": 34, "Ts": 35,
    "Jc": 36, "Jd": 37, "Jh": 38, "Js": 39,
    "Qc": 40, "Qd": 41, "Qh": 42, "Qs": 43,
    "Kc": 44, "Kd": 45, "Kh": 46, "Ks": 47,
    "Ac": 48, "Ad": 49, "Ah": 50, "As": 51,
}

UI_CLASS_MAP: dict[str, int] = {
    "fold": 52, "check": 53, "raise": 54,
    "raise_2x": 55, "raise_2_5x": 56, "raise_pot": 57,
    "raise_confirm": 58, "allin": 59,
    "pot": 60, "stack": 61,
}

FULL_CLASS_MAP = {**CARD_CLASS_MAP, **UI_CLASS_MAP}

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ──────────────────────────────────────────────
# PPPoker visual parameters
# ──────────────────────────────────────────────
# Gold border color range (BGR)
GOLD_COLORS = [
    (30, 180, 220),   # dark gold
    (40, 200, 240),   # medium gold
    (60, 215, 255),   # bright gold
    (50, 190, 230),   # standard gold
]

# PPPoker button colors (BGR)
BUTTON_COLORS = {
    "fold":  {"bg": (50, 50, 200),  "text": (255, 255, 255)},  # red
    "check": {"bg": (180, 180, 50), "text": (255, 255, 255)},  # teal
    "raise": {"bg": (180, 120, 40), "text": (255, 255, 255)},  # blue
}

# PPPoker table green (BGR)
TABLE_GREEN = (60, 100, 40)
TABLE_GREEN_RANGE = [
    (55, 95, 35), (65, 105, 45), (50, 90, 30), (70, 110, 50),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate PPPoker-realistic synthetic data for YOLO training"
    )
    p.add_argument("--assets-cards", type=str, default="assets/cards")
    p.add_argument("--assets-bg", type=str, default="assets/backgrounds")
    p.add_argument("--output", type=str, default="datasets/synthetic_v3")
    p.add_argument("--num-images", type=int, default=5000)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--split-val", type=float, default=0.15)
    # PPPoker-specific options
    p.add_argument("--gold-border", action="store_true", default=True,
                    help="Add gold borders to hero cards (PPPoker style)")
    p.add_argument("--showdown", action="store_true", default=True,
                    help="Generate opponent showdown card scenarios")
    p.add_argument("--buttons", action="store_true", default=True,
                    help="Render action buttons (fold/check/raise)")
    p.add_argument("--green-table", action="store_true", default=True,
                    help="Generate pure green table backgrounds")
    p.add_argument("--hero-pct", type=float, default=0.70,
                    help="Fraction of images with hero cards at bottom")
    p.add_argument("--showdown-pct", type=float, default=0.25,
                    help="Fraction of images with opponent showdown cards")
    p.add_argument("--button-pct", type=float, default=0.50,
                    help="Fraction of images with action buttons")
    p.add_argument("--domain-rand", action="store_true", default=True,
                    help="Apply domain randomization (blur, JPEG artifacts, glow, occlusion)")
    p.add_argument("--domain-rand-pct", type=float, default=0.70,
                    help="Fraction of images receiving domain randomization")
    # Showdown occlusion parameters
    p.add_argument("--showdown-overlap-min", type=float, default=0.40,
                    help="Minimum card overlap fraction in showdown (0.40 = 40%% hidden)")
    p.add_argument("--showdown-overlap-max", type=float, default=0.60,
                    help="Maximum card overlap fraction in showdown")
    p.add_argument("--showdown-scale-min", type=float, default=0.50,
                    help="Minimum card scale factor for showdown (vs normal)")
    p.add_argument("--showdown-scale-max", type=float, default=0.70,
                    help="Maximum card scale factor for showdown")
    p.add_argument("--showdown-min-visible", type=float, default=0.20,
                    help="Discard annotation if visible card area < this fraction")
    return p.parse_args()


# ──────────────────────────────────────────────
# Asset loading
# ──────────────────────────────────────────────
def load_card_assets(cards_dir: Path) -> list[tuple[str, np.ndarray]]:
    cards = []
    for f in sorted(cards_dir.iterdir()):
        if f.suffix.lower() != ".png":
            continue
        name = f.stem
        if name not in CARD_CLASS_MAP:
            continue
        img = cv2.imread(str(f), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        if img.shape[2] == 3:
            alpha = np.full((*img.shape[:2], 1), 255, dtype=np.uint8)
            img = np.concatenate([img, alpha], axis=2)
        cards.append((name, img))
    return cards


def load_backgrounds(bg_dir: Path) -> list[np.ndarray]:
    bgs = []
    for f in sorted(bg_dir.iterdir()):
        if f.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
            continue
        img = cv2.imread(str(f), cv2.IMREAD_COLOR)
        if img is not None:
            bgs.append(img)
    return bgs


# ──────────────────────────────────────────────
# Gold border rendering (PPPoker hero card style)
# ──────────────────────────────────────────────
def add_gold_border(card_bgra: np.ndarray, border_width: int = 3) -> np.ndarray:
    """Add a gold/amber border around the card, mimicking PPPoker hero card style.

    The gold border in PPPoker is a glowing rounded rectangle around each
    hero card.  We simulate it by drawing a colored border on the alpha-visible
    region of the card.
    """
    h, w = card_bgra.shape[:2]
    result = card_bgra.copy()

    # Choose a random gold shade
    gold_bgr = random.choice(GOLD_COLORS)

    # Create mask from alpha channel
    alpha = card_bgra[:, :, 3]
    mask = (alpha > 50).astype(np.uint8) * 255

    # Find contours of the card
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return result

    # Draw gold border on the outer edge
    bw = random.randint(max(1, border_width - 1), border_width + 2)

    # Outer glow (slightly transparent, wider)
    glow = result.copy()
    cv2.drawContours(glow, contours, -1, (*gold_bgr, 200), bw + 2)
    # Blend glow with 50% opacity
    alpha_blend = 0.4
    result = cv2.addWeighted(result, 1.0 - alpha_blend, glow, alpha_blend, 0)

    # Sharp border
    cv2.drawContours(result, contours, -1, (*gold_bgr, 255), bw)

    # Add slight golden tint to the entire card face
    tint_mask = alpha > 100
    for c in range(3):
        channel = result[:, :, c].astype(np.float32)
        channel[tint_mask] = channel[tint_mask] * 0.92 + gold_bgr[c] * 0.08
        result[:, :, c] = np.clip(channel, 0, 255).astype(np.uint8)

    return result


def add_shadow(card_bgra: np.ndarray, offset: int = 4, blur: int = 7) -> np.ndarray:
    """Add a drop shadow behind the card."""
    h, w = card_bgra.shape[:2]
    # Create larger canvas
    new_h = h + abs(offset) + blur
    new_w = w + abs(offset) + blur
    result = np.zeros((new_h, new_w, 4), dtype=np.uint8)

    # Shadow from alpha channel
    shadow = np.zeros((new_h, new_w), dtype=np.uint8)
    alpha = card_bgra[:, :, 3]
    ox = max(0, offset)
    oy = max(0, offset)
    shadow[oy:oy+h, ox:ox+w] = alpha
    shadow = cv2.GaussianBlur(shadow, (blur*2+1, blur*2+1), 0)

    # Draw shadow (dark, semi-transparent)
    result[:, :, 0] = 0
    result[:, :, 1] = 0
    result[:, :, 2] = 0
    result[:, :, 3] = (shadow * 0.5).astype(np.uint8)

    # Overlay card
    cx = max(0, -offset)
    cy = max(0, -offset)
    paste_with_alpha(result, card_bgra, cx, cy)

    return result


def paste_with_alpha(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    """Paste src BGRA onto dst BGRA at (x, y) with alpha compositing."""
    sh, sw = src.shape[:2]
    dh, dw = dst.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(dw, x + sw), min(dh, y + sh)
    if x2 <= x1 or y2 <= y1:
        return
    sx1, sy1 = x1 - x, y1 - y
    sx2, sy2 = sx1 + (x2 - x1), sy1 + (y2 - y1)

    src_alpha = src[sy1:sy2, sx1:sx2, 3:4].astype(np.float32) / 255.0
    dst_alpha = dst[y1:y2, x1:x2, 3:4].astype(np.float32) / 255.0

    out_alpha = src_alpha + dst_alpha * (1.0 - src_alpha)
    safe_alpha = np.where(out_alpha > 0, out_alpha, 1.0)

    for c in range(3):
        dst[y1:y2, x1:x2, c] = (
            (src[sy1:sy2, sx1:sx2, c].astype(np.float32) * src_alpha[:, :, 0] +
             dst[y1:y2, x1:x2, c].astype(np.float32) * dst_alpha[:, :, 0] * (1.0 - src_alpha[:, :, 0]))
            / safe_alpha[:, :, 0]
        ).astype(np.uint8)
    dst[y1:y2, x1:x2, 3] = (out_alpha[:, :, 0] * 255).astype(np.uint8)


# ──────────────────────────────────────────────
# Card augmentation
# ──────────────────────────────────────────────
def augment_card(
    card_bgra: np.ndarray,
    target_h: int,
    rotation_max: float = 5.0,
    brightness_range: float = 0.2,
    contrast_range: float = 0.2,
) -> np.ndarray:
    h_orig, w_orig = card_bgra.shape[:2]
    aspect = w_orig / h_orig
    new_h = target_h
    new_w = int(new_h * aspect)
    card = cv2.resize(card_bgra, (new_w, new_h), interpolation=cv2.INTER_AREA)

    angle = random.uniform(-rotation_max, rotation_max)
    if abs(angle) > 0.5:
        card = rotate_bgra(card, angle)

    bgr = card[:, :, :3].astype(np.float32)
    alpha = card[:, :, 3:]
    brightness = 1.0 + random.uniform(-brightness_range, brightness_range)
    contrast = 1.0 + random.uniform(-contrast_range, contrast_range)
    mean = bgr.mean()
    bgr = (bgr - mean) * contrast + mean
    bgr = bgr * brightness
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    return np.concatenate([bgr, alpha], axis=2)


def rotate_bgra(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    cx, cy = w / 2, h / 2
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    cos_a = abs(M[0, 0])
    sin_a = abs(M[0, 1])
    new_w = int(h * sin_a + w * cos_a)
    new_h = int(h * cos_a + w * sin_a)
    M[0, 2] += (new_w / 2) - cx
    M[1, 2] += (new_h / 2) - cy
    return cv2.warpAffine(
        img, M, (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )


# ──────────────────────────────────────────────
# Background generation (PPPoker green table)
# ──────────────────────────────────────────────
def generate_green_table(imgsz: int) -> np.ndarray:
    """Generate a PPPoker-style green table background (vectorized)."""
    base_color = np.array(random.choice(TABLE_GREEN_RANGE), dtype=np.float32)

    # Vectorized radial gradient (no Python loops)
    ys = np.linspace(-1, 1, imgsz).reshape(-1, 1)
    xs = np.linspace(-1, 1, imgsz).reshape(1, -1)
    dist = np.sqrt(xs * xs + ys * ys)
    dist = np.minimum(dist, 1.0)
    factor = (1.0 - dist * 0.3)[:, :, np.newaxis]  # 30% darker at edges

    bg = (base_color * factor).astype(np.float32)

    # Add subtle noise
    noise = np.random.randint(-8, 9, bg.shape, dtype=np.int16)
    bg = np.clip(bg.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # Slight gaussian blur for realism
    bg = cv2.GaussianBlur(bg, (3, 3), 0)

    return bg


# ──────────────────────────────────────────────
# Button rendering
# ──────────────────────────────────────────────
def render_button(
    bg: np.ndarray,
    text: str,
    x: int, y: int,
    w: int, h: int,
    bg_color: tuple,
    text_color: tuple = (255, 255, 255),
) -> tuple[int, int, int, int]:
    """Render a PPPoker-style action button and return (x1, y1, x2, y2)."""
    x1, y1 = x, y
    x2, y2 = x + w, y + h

    # Clamp to image bounds
    img_h, img_w = bg.shape[:2]
    x1 = max(0, min(img_w - 1, x1))
    y1 = max(0, min(img_h - 1, y1))
    x2 = max(0, min(img_w, x2))
    y2 = max(0, min(img_h, y2))

    if x2 <= x1 or y2 <= y1:
        return (0, 0, 0, 0)

    # Draw rounded rectangle (button background)
    radius = min(8, (y2 - y1) // 3)
    cv2.rectangle(bg, (x1, y1), (x2, y2), bg_color, -1)
    # Add slight border
    border_color = tuple(min(255, c + 40) for c in bg_color)
    cv2.rectangle(bg, (x1, y1), (x2, y2), border_color, 1)

    # Draw text
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.35, (y2 - y1) / 60)
    thickness = max(1, int(font_scale))
    text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
    tx = x1 + (w - text_size[0]) // 2
    ty = y1 + (h + text_size[1]) // 2
    cv2.putText(bg, text, (tx, ty), font, font_scale, text_color, thickness)

    return (x1, y1, x2, y2)


# ──────────────────────────────────────────────
# Utility functions
# ──────────────────────────────────────────────
def paste_card_on_bg(
    bg: np.ndarray,
    card_bgra: np.ndarray,
    x: int, y: int,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    bg_h, bg_w = bg.shape[:2]
    c_h, c_w = card_bgra.shape[:2]
    x1 = max(x, 0)
    y1 = max(y, 0)
    x2 = min(x + c_w, bg_w)
    y2 = min(y + c_h, bg_h)
    if x2 <= x1 or y2 <= y1:
        return bg, (0, 0, 0, 0)
    cx1, cy1 = x1 - x, y1 - y
    cx2, cy2 = cx1 + (x2 - x1), cy1 + (y2 - y1)
    card_region = card_bgra[cy1:cy2, cx1:cx2]
    alpha = card_region[:, :, 3:4].astype(np.float32) / 255.0
    card_bgr = card_region[:, :, :3].astype(np.float32)
    bg_region = bg[y1:y2, x1:x2].astype(np.float32)
    blended = card_bgr * alpha + bg_region * (1.0 - alpha)
    bg[y1:y2, x1:x2] = blended.astype(np.uint8)

    alpha_mask = card_region[:, :, 3] > 10
    if not alpha_mask.any():
        return bg, (0, 0, 0, 0)
    rows = np.any(alpha_mask, axis=1)
    cols = np.any(alpha_mask, axis=0)
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    return bg, (int(x1 + cmin), int(y1 + rmin), int(x1 + cmax), int(y1 + rmax))


def bbox_to_yolo(bbox: tuple, img_w: int, img_h: int) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    xc = (x1 + x2) / 2.0 / img_w
    yc = (y1 + y2) / 2.0 / img_h
    w = (x2 - x1) / img_w
    h = (y2 - y1) / img_h
    return (round(xc, 6), round(yc, 6), round(w, 6), round(h, 6))


def compute_iou(a: tuple, b: tuple) -> float:
    xa = max(a[0], b[0])
    ya = max(a[1], b[1])
    xb = min(a[2], b[2])
    yb = min(a[3], b[3])
    inter = max(0, xb - xa) * max(0, yb - ya)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter) if (area_a + area_b - inter) > 0 else 0.0


def augment_background(bg: np.ndarray) -> np.ndarray:
    """Apply light augmentation to background."""
    img = bg.astype(np.float32)
    brightness = 1.0 + random.uniform(-0.15, 0.15)
    contrast = 1.0 + random.uniform(-0.15, 0.15)
    mean = img.mean()
    img = (img - mean) * contrast + mean
    img = img * brightness

    # Random hue shift
    if random.random() < 0.3:
        hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
        hsv[:, :, 0] = (hsv[:, :, 0].astype(int) + random.randint(-5, 5)) % 180
        img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).astype(np.float32)

    return np.clip(img, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────
# Showdown realism — perspective, occlusion, smart annotation
# ──────────────────────────────────────────────

# Opponent seat positions on 6-max PPPoker table (normalised x, y)
# Each entry: (cx, cy, perspective_label)
OPPONENT_SEATS = [
    (0.20, 0.15, "top-left"),      # Seat 1 — top-left
    (0.50, 0.10, "top-center"),    # Seat 2 — top-center
    (0.80, 0.15, "top-right"),     # Seat 3 — top-right
    (0.13, 0.45, "left"),          # Seat 4 — mid-left
    (0.87, 0.45, "right"),         # Seat 5 — mid-right
]


def perspective_warp(
    card_bgra: np.ndarray,
    position: str = "top-center",
    intensity: float = 0.5,
) -> np.ndarray:
    """Apply perspective distortion simulating camera angle at table periphery.

    Cards far from the camera center (hero's viewpoint at bottom) appear
    skewed.  ``position`` describes the card's location on the table;
    ``intensity`` (0-1) controls how extreme the warp is.

    Supported positions: top-left, top-center, top-right, left, right.
    """
    h, w = card_bgra.shape[:2]
    intensity = max(0.0, min(1.0, intensity))
    max_shift = int(min(w, h) * 0.15 * intensity)
    if max_shift < 2:
        return card_bgra

    def _r(hi: int) -> int:
        return random.randint(0, max(1, hi))

    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])

    if position == "top-left":
        # Top-left shrinks, bottom-right stays → lean towards right
        dst = np.float32([
            [_r(max_shift), _r(max_shift)],
            [w - _r(max_shift // 2), _r(max_shift)],
            [w, h],
            [_r(max_shift), h - _r(max_shift // 2)],
        ])
    elif position == "top-right":
        dst = np.float32([
            [_r(max_shift // 2), _r(max_shift)],
            [w - _r(max_shift), _r(max_shift)],
            [w - _r(max_shift), h - _r(max_shift // 2)],
            [0, h],
        ])
    elif position == "top-center":
        s = _r(max_shift)
        dst = np.float32([
            [s, s],
            [w - s, s],
            [w, h],
            [0, h],
        ])
    elif position == "left":
        s = _r(max_shift)
        dst = np.float32([
            [s, s // 2],
            [w, 0],
            [w, h],
            [s, h - s // 2],
        ])
    elif position == "right":
        s = _r(max_shift)
        dst = np.float32([
            [0, 0],
            [w - s, s // 2],
            [w - s, h - s // 2],
            [0, h],
        ])
    else:
        s = max_shift // 2
        dst = np.float32([
            [_r(s), _r(s)],
            [w - _r(s), _r(s)],
            [w - _r(s), h - _r(s)],
            [_r(s), h - _r(s)],
        ])

    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        card_bgra, M, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )


def apply_occlusion_simulation(
    hand_cards: list[tuple[str, np.ndarray]],
    card_h: int,
    overlap_pct: float = 0.50,
    position: str = "top-center",
    perspective_intensity: float = 0.5,
    min_visible_frac: float = 0.20,
) -> tuple[np.ndarray, list[tuple[str, tuple[int, int, int, int], float]]]:
    """Arrange cards with heavy overlap and track per-card visibility.

    Simulates PLO5/PLO6 showdown where 5-6 cards are stacked tightly,
    often revealing only the rank/suit index corner.

    Args:
        hand_cards: list of (card_name, card_bgra) for one opponent hand.
        card_h: target card height in pixels.
        overlap_pct: fraction of card width hidden by the next card (0.40-0.60).
        position: table position for perspective warp.
        perspective_intensity: how much perspective distortion (0-1).
        min_visible_frac: discard annotation if visible area < this fraction.

    Returns:
        composite_bgra: BGRA image of the assembled hand (tight crop).
        card_annotations: list of (card_name, visible_bbox_on_composite, vis_frac)
            where visible_bbox is (x1, y1, x2, y2) in composite coords.
            Cards with vis_frac < min_visible_frac are excluded.
    """
    if not hand_cards:
        return np.zeros((1, 1, 4), dtype=np.uint8), []

    # ── Prepare individual card images ──
    prepared: list[tuple[str, np.ndarray]] = []
    for card_name, card_img in hand_cards:
        aspect = card_img.shape[1] / card_img.shape[0]
        c_w = int(card_h * aspect)
        resized = cv2.resize(card_img, (c_w, card_h), interpolation=cv2.INTER_AREA)

        # Apply perspective warp
        resized = perspective_warp(resized, position, perspective_intensity)

        # Light augmentation (rotation, brightness)
        resized = augment_card(resized, card_h, rotation_max=4.0,
                               brightness_range=0.25, contrast_range=0.2)

        # Slight fade (opponent cards look dimmer)
        if random.random() < 0.5:
            bgr = resized[:, :, :3].astype(np.float32)
            bgr *= random.uniform(0.65, 0.90)
            resized[:, :, :3] = np.clip(bgr, 0, 255).astype(np.uint8)

        prepared.append((card_name, resized))

    # ── Compute composite canvas size ──
    max_h = max(c.shape[0] for _, c in prepared)
    card_widths = [c.shape[1] for _, c in prepared]
    avg_w = int(np.mean(card_widths))
    step_x = max(4, int(avg_w * (1.0 - overlap_pct)))
    canvas_w = step_x * (len(prepared) - 1) + max(card_widths) + 4
    canvas_h = max_h + 10  # padding for rotation overhang

    # ── Place cards & build per-card alpha masks ──
    canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    placements: list[tuple[str, np.ndarray]] = []  # (name, alpha_mask on canvas)

    for i, (card_name, card_img) in enumerate(prepared):
        ch, cw = card_img.shape[:2]
        px = i * step_x
        py = random.randint(0, max(0, canvas_h - ch))

        # Build alpha mask BEFORE pasting (so we know where this card is)
        mask = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
        y1, y2 = py, min(py + ch, canvas_h)
        x1, x2 = px, min(px + cw, canvas_w)
        src_y = y2 - py
        src_x = x2 - px
        mask[y1:y2, x1:x2] = card_img[:src_y, :src_x, 3]

        placements.append((card_name, mask))
        paste_with_alpha(canvas, card_img, px, py)

    # ── Compute visibility for each card ──
    annotations: list[tuple[str, tuple[int, int, int, int], float]] = []
    num_cards = len(placements)

    for idx, (card_name, my_mask) in enumerate(placements):
        # My total area = pixels where my alpha > 10
        total_pixels = int(np.count_nonzero(my_mask > 10))
        if total_pixels == 0:
            continue

        # Build occlusion mask: union of all cards placed AFTER me
        occluder = np.zeros_like(my_mask)
        for later_idx in range(idx + 1, num_cards):
            later_mask = placements[later_idx][1]
            occluder = np.maximum(occluder, later_mask)

        # Visible pixels = my pixels that are NOT covered by later cards
        visible = (my_mask > 10) & (occluder < 50)
        visible_pixels = int(np.count_nonzero(visible))
        vis_frac = visible_pixels / total_pixels

        if vis_frac < min_visible_frac:
            continue  # Card too occluded — skip annotation

        # Bounding box of visible pixels
        rows = np.any(visible, axis=1)
        cols = np.any(visible, axis=0)
        if not rows.any() or not cols.any():
            continue
        rmin, rmax = int(np.where(rows)[0][0]), int(np.where(rows)[0][-1])
        cmin, cmax = int(np.where(cols)[0][0]), int(np.where(cols)[0][-1])

        # Sanity: bbox must be at least 3px in each dimension
        if (cmax - cmin) < 3 or (rmax - rmin) < 3:
            continue

        annotations.append((card_name, (cmin, rmin, cmax, rmax), vis_frac))

    return canvas, annotations


def render_showdown_layer(
    bg: np.ndarray,
    all_cards: list[tuple[str, np.ndarray]],
    imgsz: int,
    used_cards: set[str],
    num_opponents: int | None = None,
    scale_range: tuple[float, float] = (0.50, 0.70),
    overlap_range: tuple[float, float] = (0.40, 0.60),
    min_visible_frac: float = 0.20,
) -> tuple[np.ndarray, list[tuple[int, float, float, float, float]], set[str]]:
    """Render realistic opponent showdown hands on the background.

    Places 1-3 opponent hands around the table periphery with:
      - Cards at 50-70% of normal scale
      - Heavy PLO5/PLO6 card overlap (40-60% hidden)
      - Perspective distortion based on table position
      - Bounding boxes focused on visible card area only
      - Automatic discard when card is <20% visible

    Args:
        bg: Background image (BGR, HxW).
        all_cards: Full deck of (name, bgra) assets.
        imgsz: Image dimension (square).
        used_cards: Set of card names already placed on the board/hero hand.
        num_opponents: How many opponents show their hand (1-3, random if None).
        scale_range: Card height as fraction of imgsz (0.50-0.70 of normal).
        overlap_range: Fraction of card width hidden by next card.
        min_visible_frac: Minimum visible area to keep annotation.

    Returns:
        bg: Updated background with showdown cards composited.
        labels: YOLO-format labels [(class_id, xc, yc, w, h), ...].
        used_cards: Updated set of used card names.
    """
    labels: list[tuple[int, float, float, float, float]] = []

    if num_opponents is None:
        num_opponents = random.choices([1, 2, 3], weights=[0.35, 0.45, 0.20])[0]

    # Pick opponent seats (random subset)
    seats = random.sample(OPPONENT_SEATS, min(num_opponents, len(OPPONENT_SEATS)))

    for seat_cx, seat_cy, seat_pos in seats:
        # Cards per hand: PLO5=5, PLO6=6, occasionally 4 (PLO4)
        num_cards = random.choices([4, 5, 6], weights=[0.15, 0.40, 0.45])[0]

        available = [(n, img) for n, img in all_cards if n not in used_cards]
        if len(available) < num_cards:
            break
        random.shuffle(available)
        hand = available[:num_cards]
        for n, _ in hand:
            used_cards.add(n)

        # Scale: showdown cards are 50-70% of the normal card size for that zone
        # Normal opponent card_scale ≈ 0.08-0.11, so showdown ≈ 0.04-0.077
        base_scale = random.uniform(0.07, 0.12)
        showdown_scale = base_scale * random.uniform(*scale_range)
        card_h = max(20, int(imgsz * showdown_scale))

        overlap = random.uniform(*overlap_range)
        persp_intensity = random.uniform(0.3, 0.8)

        # Build the hand composite with occlusion tracking
        composite, card_annots = apply_occlusion_simulation(
            hand_cards=hand,
            card_h=card_h,
            overlap_pct=overlap,
            position=seat_pos,
            perspective_intensity=persp_intensity,
            min_visible_frac=min_visible_frac,
        )

        comp_h, comp_w = composite.shape[:2]
        if comp_w < 4 or comp_h < 4:
            continue

        # Position the composite on the background (centered on seat)
        paste_x = int(seat_cx * imgsz - comp_w / 2) + random.randint(-20, 20)
        paste_y = int(seat_cy * imgsz - comp_h / 2) + random.randint(-10, 10)

        # Clamp to image bounds
        paste_x = max(0, min(imgsz - comp_w, paste_x))
        paste_y = max(0, min(imgsz - comp_h, paste_y))

        # Paste composite onto background (convert BGRA → BGR with alpha)
        comp_alpha = composite[:, :, 3:4].astype(np.float32) / 255.0
        comp_bgr = composite[:, :, :3].astype(np.float32)

        # Ensure we don't exceed bg bounds
        ey = min(paste_y + comp_h, imgsz)
        ex = min(paste_x + comp_w, imgsz)
        ch_actual = ey - paste_y
        cw_actual = ex - paste_x

        if ch_actual < 1 or cw_actual < 1:
            continue

        bg_region = bg[paste_y:ey, paste_x:ex].astype(np.float32)
        a = comp_alpha[:ch_actual, :cw_actual]
        blended = comp_bgr[:ch_actual, :cw_actual] * a + bg_region * (1.0 - a)
        bg[paste_y:ey, paste_x:ex] = blended.astype(np.uint8)

        # Convert per-card annotations to YOLO format
        for card_name, (vx1, vy1, vx2, vy2), vis_frac in card_annots:
            # Translate from composite coords to bg coords
            bx1 = paste_x + vx1
            by1 = paste_y + vy1
            bx2 = paste_x + vx2
            by2 = paste_y + vy2

            # Clamp to image
            bx1 = max(0, min(imgsz - 1, bx1))
            by1 = max(0, min(imgsz - 1, by1))
            bx2 = max(1, min(imgsz, bx2))
            by2 = max(1, min(imgsz, by2))

            if (bx2 - bx1) < 3 or (by2 - by1) < 3:
                continue

            yolo = bbox_to_yolo((bx1, by1, bx2, by2), imgsz, imgsz)
            if all(0 < v < 1 for v in yolo[:2]) and yolo[2] > 0.003:
                labels.append((CARD_CLASS_MAP[card_name], *yolo))

    return bg, labels, used_cards


# ──────────────────────────────────────────────
# Scene generation — PPPoker layout
# ──────────────────────────────────────────────
def generate_pppoker_scene(
    backgrounds: list[np.ndarray],
    cards: list[tuple[str, np.ndarray]],
    imgsz: int,
    use_gold_border: bool = True,
    include_showdown: bool = False,
    include_buttons: bool = False,
    use_green_table: bool = True,
    showdown_overlap: tuple[float, float] = (0.40, 0.60),
    showdown_scale: tuple[float, float] = (0.50, 0.70),
    showdown_min_visible: float = 0.20,
) -> tuple[np.ndarray, list[tuple[int, float, float, float, float]]]:
    """Generate one PPPoker-realistic scene.

    Layout (normalized to imgsz):
        - Opponent cards:     periphery seats (showdown only, with occlusion)
        - Board cards:        center 35-50%
        - Hero cards:         bottom 72-88% (with gold borders)
        - Action buttons:     bottom 90-98%
    """
    # Background
    if use_green_table and random.random() < 0.6:
        bg = generate_green_table(imgsz)
    else:
        bg_orig = random.choice(backgrounds) if backgrounds else generate_green_table(imgsz)
        bg = cv2.resize(bg_orig, (imgsz, imgsz), interpolation=cv2.INTER_AREA)
        bg = augment_background(bg)

    labels: list[tuple[int, float, float, float, float]] = []
    used_cards: set[str] = set()

    # ── Board cards (3-5 cards, center of table) ──
    num_board = random.choice([0, 3, 3, 4, 4, 5, 5])
    board_selected = []
    available = [(n, img) for n, img in cards if n not in used_cards]
    random.shuffle(available)
    for n, img in available[:num_board]:
        board_selected.append((n, img))
        used_cards.add(n)

    if board_selected:
        card_scale = random.uniform(0.10, 0.16)
        card_h = int(imgsz * card_scale)
        est_w = int(card_h * 0.7)
        gap = random.randint(2, 8)
        total_w = len(board_selected) * est_w + (len(board_selected) - 1) * gap
        start_x = (imgsz - total_w) // 2 + random.randint(-20, 20)
        base_y = int(imgsz * random.uniform(0.35, 0.48))

        for i, (card_name, card_img) in enumerate(board_selected):
            aug = augment_card(card_img, card_h, rotation_max=3.0)
            px = start_x + i * (est_w + gap)
            py = base_y + random.randint(-5, 5)
            bg, bbox = paste_card_on_bg(bg, aug, px, py)
            if bbox[2] - bbox[0] > 3 and bbox[3] - bbox[1] > 3:
                yolo = bbox_to_yolo(bbox, imgsz, imgsz)
                if all(0 < v < 1 for v in yolo[:2]) and yolo[2] > 0.005:
                    labels.append((CARD_CLASS_MAP[card_name], *yolo))

    # ── Hero cards (4-6 cards, bottom with gold border) ──
    num_hero = random.choice([4, 4, 5, 5, 6, 6])
    available = [(n, img) for n, img in cards if n not in used_cards]
    random.shuffle(available)
    hero_selected = available[:num_hero]
    for n, _ in hero_selected:
        used_cards.add(n)

    if hero_selected:
        card_scale = random.uniform(0.08, 0.14)
        card_h = int(imgsz * card_scale)
        est_w = int(card_h * 0.7)
        overlap_frac = random.uniform(0.25, 0.50)
        step_x = int(est_w * (1.0 - overlap_frac))
        step_x = max(8, step_x)
        total_w = step_x * (len(hero_selected) - 1) + est_w
        start_x = (imgsz - total_w) // 2 + random.randint(-15, 15)
        base_y = int(imgsz * random.uniform(0.73, 0.86))

        max_fan = random.uniform(2.0, 8.0)

        for i, (card_name, card_img) in enumerate(hero_selected):
            # Apply gold border if enabled
            if use_gold_border:
                card_img = add_gold_border(card_img, border_width=random.randint(2, 4))

            # Fan rotation
            if len(hero_selected) > 1:
                t = (i / (len(hero_selected) - 1)) - 0.5
                fan_angle = t * max_fan * 2
            else:
                fan_angle = 0

            aug = augment_card(card_img, card_h, rotation_max=abs(fan_angle) + 2.0)

            px = start_x + i * step_x
            arc = int(abs(i - (len(hero_selected) - 1) / 2) * 2)
            py = base_y - arc + random.randint(-3, 3)

            bg, bbox = paste_card_on_bg(bg, aug, px, py)
            if bbox[2] - bbox[0] > 3 and bbox[3] - bbox[1] > 3:
                yolo = bbox_to_yolo(bbox, imgsz, imgsz)
                if all(0 < v < 1 for v in yolo[:2]) and yolo[2] > 0.005:
                    labels.append((CARD_CLASS_MAP[card_name], *yolo))

    # ── Opponent showdown cards (realistic PLO5/PLO6 occlusion) ──
    if include_showdown:
        bg, showdown_labels, used_cards = render_showdown_layer(
            bg=bg,
            all_cards=cards,
            imgsz=imgsz,
            used_cards=used_cards,
            scale_range=showdown_scale,
            overlap_range=showdown_overlap,
            min_visible_frac=showdown_min_visible,
        )
        labels.extend(showdown_labels)

    # ── Action buttons (fold / check / raise at bottom) ──
    if include_buttons and random.random() < 0.8:
        btn_h = int(imgsz * random.uniform(0.055, 0.08))
        btn_w = int(imgsz * random.uniform(0.25, 0.32))
        btn_gap = random.randint(3, 10)
        btn_y = int(imgsz * random.uniform(0.90, 0.96))

        total_btn_w = 3 * btn_w + 2 * btn_gap
        btn_start_x = (imgsz - total_btn_w) // 2

        button_order = [
            ("Fold", "fold", BUTTON_COLORS["fold"]["bg"]),
            ("Check", "check", BUTTON_COLORS["check"]["bg"]),
            ("Bet", "raise", BUTTON_COLORS["raise"]["bg"]),
        ]

        for j, (text, cls_name, color) in enumerate(button_order):
            bx = btn_start_x + j * (btn_w + btn_gap)
            # Slight color variation
            color_var = tuple(
                max(0, min(255, c + random.randint(-15, 15)))
                for c in color
            )
            bbox = render_button(bg, text, bx, btn_y, btn_w, btn_h, color_var)
            if bbox[2] - bbox[0] > 3 and bbox[3] - bbox[1] > 3:
                yolo = bbox_to_yolo(bbox, imgsz, imgsz)
                if all(0 < v < 1 for v in yolo[:2]) and yolo[2] > 0.005:
                    labels.append((UI_CLASS_MAP[cls_name], *yolo))

    return bg, labels


# ──────────────────────────────────────────────
# Domain Randomization (Sim2Real)
# Ref: https://lilianweng.github.io/posts/2019-05-05-domain-randomization/
# ──────────────────────────────────────────────
def domain_randomize(image: np.ndarray) -> np.ndarray:
    """Apply aggressive domain randomization to bridge the sim2real gap.

    Simulates real-world artefacts that the emulator produces:
      - Motion blur from screen capture lag
      - JPEG compression artefacts from mss/screenshot
      - Emulator glow/bloom effects around gold borders
      - Gamma/color channel shifts from LDPlayer rendering
      - Gaussian noise from low-quality capture
      - Partial occlusion (random dark rectangles = chat bubbles, overlays)
    """
    h, w = image.shape[:2]
    result = image.copy()

    # ── 1. Motion blur (emulator lag during capture) ──
    if random.random() < 0.30:
        ksize = random.choice([3, 5, 7])
        direction = random.choice(["horizontal", "vertical", "diagonal"])
        kernel = np.zeros((ksize, ksize), dtype=np.float32)
        if direction == "horizontal":
            kernel[ksize // 2, :] = 1.0 / ksize
        elif direction == "vertical":
            kernel[:, ksize // 2] = 1.0 / ksize
        else:  # diagonal
            for i in range(ksize):
                kernel[i, i] = 1.0 / ksize
        result = cv2.filter2D(result, -1, kernel)

    # ── 2. JPEG compression artefacts ──
    if random.random() < 0.40:
        quality = random.randint(40, 85)
        _, enc = cv2.imencode(".jpg", result, [cv2.IMWRITE_JPEG_QUALITY, quality])
        result = cv2.imdecode(enc, cv2.IMREAD_COLOR)

    # ── 3. Glow/bloom around bright regions (gold border artifacts) ──
    if random.random() < 0.25:
        hsv = cv2.cvtColor(result, cv2.COLOR_BGR2HSV)
        bright_mask = hsv[:, :, 2] > 200
        if bright_mask.any():
            glow = np.zeros_like(result, dtype=np.float32)
            glow[bright_mask] = result[bright_mask].astype(np.float32)
            glow = cv2.GaussianBlur(glow, (0, 0), sigmaX=random.uniform(3, 8))
            alpha = random.uniform(0.15, 0.35)
            result = np.clip(
                result.astype(np.float32) + glow * alpha, 0, 255
            ).astype(np.uint8)

    # ── 4. Gamma / channel shift (LDPlayer color rendering) ──
    if random.random() < 0.35:
        gamma = random.uniform(0.7, 1.4)
        inv_gamma = 1.0 / gamma
        table = np.array([
            ((i / 255.0) ** inv_gamma) * 255 for i in range(256)
        ]).astype("uint8")
        # Apply to random subset of channels
        channels = list(range(3))
        if random.random() < 0.5:
            channels = random.sample(channels, random.randint(1, 2))
        for c in channels:
            result[:, :, c] = cv2.LUT(result[:, :, c], table)

    # ── 5. Gaussian noise (capture noise) ──
    if random.random() < 0.35:
        sigma = random.uniform(3, 12)
        noise = np.random.normal(0, sigma, result.shape).astype(np.float32)
        result = np.clip(result.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    # ── 6. Partial occlusion (chat bubbles, overlays, notifications) ──
    if random.random() < 0.20:
        num_rects = random.randint(1, 3)
        for _ in range(num_rects):
            rw = random.randint(w // 10, w // 4)
            rh = random.randint(h // 15, h // 6)
            rx = random.randint(0, w - rw)
            ry = random.randint(0, h - rh)
            # Semi-transparent dark rectangle
            overlay = result.copy()
            color = (
                random.randint(10, 50),
                random.randint(10, 50),
                random.randint(10, 50),
            )
            cv2.rectangle(overlay, (rx, ry), (rx + rw, ry + rh), color, -1)
            alpha = random.uniform(0.3, 0.7)
            result = cv2.addWeighted(result, 1 - alpha, overlay, alpha, 0)

    # ── 7. Random crop-and-resize (slight scale jitter) ──
    if random.random() < 0.15:
        margin = random.randint(2, int(min(h, w) * 0.05))
        cropped = result[margin:h - margin, margin:w - margin]
        result = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    return result


# ──────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)

    cards_dir = Path(args.assets_cards)
    if not cards_dir.is_absolute():
        cards_dir = PROJECT_ROOT / cards_dir

    bg_dir = Path(args.assets_bg)
    if not bg_dir.is_absolute():
        bg_dir = PROJECT_ROOT / bg_dir

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    if not cards_dir.exists():
        print(f"[ERRO] Card assets not found: {cards_dir}")
        sys.exit(1)

    print("[INFO] Loading assets...")
    cards = load_card_assets(cards_dir)
    backgrounds = load_backgrounds(bg_dir) if bg_dir.exists() else []

    if not cards:
        print(f"[ERRO] No cards found in {cards_dir}")
        sys.exit(1)

    print(f"  Cards loaded: {len(cards)}/52")
    print(f"  Backgrounds: {len(backgrounds)}")
    print(f"  Gold borders: {args.gold_border}")
    print(f"  Showdown cards: {args.showdown} ({args.showdown_pct:.0%})")
    print(f"  Showdown overlap: {args.showdown_overlap_min:.0%}-{args.showdown_overlap_max:.0%}")
    print(f"  Showdown scale:   {args.showdown_scale_min:.0%}-{args.showdown_scale_max:.0%}")
    print(f"  Showdown min vis: {args.showdown_min_visible:.0%} (discard below)")
    print(f"  Buttons: {args.buttons} ({args.button_pct:.0%})")
    print(f"  Green table BGs: {args.green_table}")
    print(f"  Domain Rand:  {args.domain_rand} ({args.domain_rand_pct:.0%})")

    # Create output directories
    num_val = int(args.num_images * args.split_val) if args.split_val > 0 else 0
    num_train = args.num_images - num_val

    splits = [("train", num_train)]
    if num_val > 0:
        splits.append(("val", num_val))

    for split_name, _ in splits:
        (output_dir / "images" / split_name).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split_name).mkdir(parents=True, exist_ok=True)

    # Generate
    print(f"\n[INFO] Generating {args.num_images} PPPoker-realistic images...")
    print(f"  Train: {num_train} | Val: {num_val}")
    print(f"  Image size: {args.imgsz}x{args.imgsz}")
    print()

    total_labels = 0
    total_hero_labels = 0
    total_button_labels = 0
    total_opp_labels = 0
    img_idx = 0

    for split_name, split_count in splits:
        img_dir = output_dir / "images" / split_name
        lbl_dir = output_dir / "labels" / split_name

        for i in tqdm(range(split_count), desc=f"Generating {split_name}", unit="img"):
            include_showdown = args.showdown and random.random() < args.showdown_pct
            include_buttons = args.buttons and random.random() < args.button_pct

            image, scene_labels = generate_pppoker_scene(
                backgrounds=backgrounds,
                cards=cards,
                imgsz=args.imgsz,
                use_gold_border=args.gold_border,
                include_showdown=include_showdown,
                include_buttons=include_buttons,
                use_green_table=args.green_table,
                showdown_overlap=(args.showdown_overlap_min, args.showdown_overlap_max),
                showdown_scale=(args.showdown_scale_min, args.showdown_scale_max),
                showdown_min_visible=args.showdown_min_visible,
            )

            # Domain Randomization (sim2real bridge)
            if args.domain_rand and random.random() < args.domain_rand_pct:
                image = domain_randomize(image)

            # Save image
            img_name = f"ppk_{img_idx:05d}.jpg"
            cv2.imwrite(str(img_dir / img_name), image, [cv2.IMWRITE_JPEG_QUALITY, 95])

            # Save labels
            lbl_name = f"ppk_{img_idx:05d}.txt"
            with open(lbl_dir / lbl_name, "w") as f:
                for class_id, xc, yc, wn, hn in scene_labels:
                    f.write(f"{class_id} {xc} {yc} {wn} {hn}\n")

            # Stats
            for cls_id, *_ in scene_labels:
                if cls_id < 52:
                    total_hero_labels += 1  # rough count
                elif cls_id >= 52:
                    total_button_labels += 1

            total_labels += len(scene_labels)
            img_idx += 1

    # Generate classes.txt and data.yaml
    classes_file = output_dir / "classes.txt"
    sorted_classes = sorted(FULL_CLASS_MAP.items(), key=lambda x: x[1])
    with open(classes_file, "w") as f:
        for name, idx in sorted_classes:
            f.write(f"{idx}: {name}\n")

    data_yaml = output_dir / "data.yaml"
    with open(data_yaml, "w") as f:
        f.write("# PPPoker-realistic synthetic dataset\n")
        f.write(f"path: {output_dir.as_posix()}\n")
        f.write("train: images/train\n")
        if num_val > 0:
            f.write("val: images/val\n")
        f.write(f"\nnc: {len(FULL_CLASS_MAP)}\n\nnames:\n")
        for name, idx in sorted_classes:
            f.write(f"  {idx}: {name}\n")

    print(f"\n{'=' * 55}")
    print(f"[OK] Generation complete!")
    print(f"  Images:     {img_idx}")
    print(f"  Labels:     {total_labels}")
    print(f"  Avg/image:  {total_labels / max(img_idx, 1):.1f}")
    print(f"  Card labels:   {total_hero_labels}")
    print(f"  Button labels: {total_button_labels}")
    print(f"  Showdown imgs: {args.showdown_pct:.0%} of total")
    print(f"  Output:     {output_dir}")
    print(f"{'=' * 55}")
    print(f"\nTo train with this data:")
    print(f"  python training/train_yolo.py \\")
    print(f"    --data {data_yaml} \\")
    print(f"    --model yolov8m.pt \\")
    print(f"    --epochs 150 --batch 16 --imgsz 640 \\")
    print(f"    --name titan_v7_ppk")
    print()
    print(f"To combine with real annotated data, update training/data.yaml:")
    print(f"  train:")
    print(f"    - {(output_dir / 'images/train').as_posix()}")
    print(f"    - titan_cards/images/train")
    print(f"  val:")
    print(f"    - {(output_dir / 'images/val').as_posix()}")
    print(f"    - titan_cards/images/val")


if __name__ == "__main__":
    main()

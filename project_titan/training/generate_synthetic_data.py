"""
Project Titan — Synthetic Card Data Generator
=============================================

Gera imagens sintéticas de cartas de poker sobre backgrounds de mesas virtuais,
com labels no formato YOLO para treino de detecção.

Uso:
    python training/generate_synthetic_data.py
    python training/generate_synthetic_data.py --num-images 5000 --cards-min 4 --cards-max 9
    python training/generate_synthetic_data.py --assets-cards assets/cards --assets-bg assets/backgrounds

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
# Mapeamento de classes (idêntico ao data.yaml)
# ──────────────────────────────────────────────
CLASS_MAP: dict[str, int] = {
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

# Full class map for data.yaml / classes.txt output (must match training/data.yaml).
# The generator only produces card labels (0–51), but the output metadata
# must declare all 62 classes so datasets are directly compatible with the
# 62-class model.
FULL_CLASS_MAP: dict[str, int] = {
    **CLASS_MAP,
    "btn_fold": 52, "btn_call": 53, "btn_raise": 54,
    "btn_raise_2x": 55, "btn_raise_2_5x": 56, "btn_raise_pot": 57,
    "btn_raise_confirm": 58, "btn_allin": 59,
    "pot": 60, "stack": 61,
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ──────────────────────────────────────────────
# Argumentos de linha de comando
# ──────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gera dados sintéticos de cartas de poker para treino YOLO"
    )
    p.add_argument(
        "--assets-cards", type=str, default="assets/cards",
        help="Pasta com PNGs das cartas (ex: Ah.png, Kd.png). Relativo a PROJECT_ROOT.",
    )
    p.add_argument(
        "--assets-bg", type=str, default="assets/backgrounds",
        help="Pasta com prints de mesas vazias (backgrounds). Relativo a PROJECT_ROOT.",
    )
    p.add_argument(
        "--output", type=str, default="datasets/synthetic",
        help="Pasta raiz de saída. Relativo a PROJECT_ROOT.",
    )
    p.add_argument("--num-images", type=int, default=2000, help="Quantidade de imagens a gerar")
    p.add_argument("--cards-min", type=int, default=2, help="Mínimo de cartas por imagem")
    p.add_argument("--cards-max", type=int, default=9, help="Máximo de cartas por imagem")
    p.add_argument("--imgsz", type=int, default=640, help="Tamanho do output (imgsz x imgsz)")
    p.add_argument("--card-scale-min", type=float, default=0.08,
                    help="Escala mínima da carta relativa ao background")
    p.add_argument("--card-scale-max", type=float, default=0.18,
                    help="Escala máxima da carta relativa ao background")
    p.add_argument("--rotation-max", type=float, default=10.0,
                    help="Rotação máxima em graus (+/-)")
    p.add_argument("--max-overlap", type=float, default=0.35,
                    help="IoU máxima permitida entre cartas (0-1)")
    p.add_argument("--brightness-range", type=float, default=0.3,
                    help="Variação de brilho (+/- fração)")
    p.add_argument("--contrast-range", type=float, default=0.3,
                    help="Variação de contraste (+/- fração)")
    p.add_argument("--seed", type=int, default=42, help="Seed para reprodutibilidade")
    p.add_argument("--split-val", type=float, default=0.15,
                    help="Fração das imagens para validação (0 = só train)")
    p.add_argument("--hand-ratio", type=float, default=0.40,
                    help="Fração das imagens que simulam mão do jogador (0-1)")
    p.add_argument("--hand-cards-min", type=int, default=2,
                    help="Mínimo de cartas na mão do jogador")
    p.add_argument("--hand-cards-max", type=int, default=6,
                    help="Máximo de cartas na mão do jogador")
    p.add_argument("--hand-scale-min", type=float, default=0.06,
                    help="Escala mínima de cartas na mão (relativa à imagem)")
    p.add_argument("--hand-scale-max", type=float, default=0.12,
                    help="Escala máxima de cartas na mão (relativa à imagem)")
    return p.parse_args()


# ──────────────────────────────────────────────
# Carregamento de assets
# ──────────────────────────────────────────────
def load_card_assets(cards_dir: Path) -> list[tuple[str, np.ndarray]]:
    """
    Carrega todas as cartas PNG (com canal alpha) da pasta.
    Retorna lista de (nome_classe, imagem_BGRA).
    """
    cards = []
    valid_ext = {".png", ".PNG"}

    for f in sorted(cards_dir.iterdir()):
        if f.suffix not in valid_ext:
            continue
        name = f.stem  # Ex: "Ah", "Kd", "2c"
        if name not in CLASS_MAP:
            print(f"  [WARN] Arquivo ignorado (nome não reconhecido): {f.name}")
            continue
        img = cv2.imread(str(f), cv2.IMREAD_UNCHANGED)  # BGRA
        if img is None:
            print(f"  [WARN] Falha ao carregar: {f.name}")
            continue
        # Garantir 4 canais
        if img.shape[2] == 3:
            alpha = np.full((*img.shape[:2], 1), 255, dtype=np.uint8)
            img = np.concatenate([img, alpha], axis=2)
        cards.append((name, img))

    return cards


def load_backgrounds(bg_dir: Path) -> list[np.ndarray]:
    """Carrega backgrounds (JPG/PNG) como BGR."""
    bgs = []
    valid_ext = {".png", ".jpg", ".jpeg", ".bmp", ".PNG", ".JPG", ".JPEG"}

    for f in sorted(bg_dir.iterdir()):
        if f.suffix not in valid_ext:
            continue
        img = cv2.imread(str(f), cv2.IMREAD_COLOR)  # BGR
        if img is None:
            print(f"  [WARN] Falha ao carregar background: {f.name}")
            continue
        bgs.append(img)

    return bgs


# ──────────────────────────────────────────────
# Augmentations na carta individual
# ──────────────────────────────────────────────
def augment_card(
    card_bgra: np.ndarray,
    target_h: int,
    rotation_max: float,
    brightness_range: float,
    contrast_range: float,
) -> np.ndarray:
    """
    Aplica resize, rotação e variação de brilho/contraste a uma carta BGRA.
    Retorna imagem BGRA augmentada.
    """
    h_orig, w_orig = card_bgra.shape[:2]
    aspect = w_orig / h_orig
    new_h = target_h
    new_w = int(new_h * aspect)
    card = cv2.resize(card_bgra, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # ── Rotação aleatória ──
    angle = random.uniform(-rotation_max, rotation_max)
    if abs(angle) > 0.5:
        card = rotate_bgra(card, angle)

    # ── Brilho e contraste (só nos canais BGR, preserva alpha) ──
    bgr = card[:, :, :3].astype(np.float32)
    alpha = card[:, :, 3:]

    brightness_factor = 1.0 + random.uniform(-brightness_range, brightness_range)
    contrast_factor = 1.0 + random.uniform(-contrast_range, contrast_range)

    # Contraste: escala em torno da média
    mean = bgr.mean()
    bgr = (bgr - mean) * contrast_factor + mean
    # Brilho
    bgr = bgr * brightness_factor

    bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    return np.concatenate([bgr, alpha], axis=2)


def rotate_bgra(img: np.ndarray, angle: float) -> np.ndarray:
    """Rotaciona imagem BGRA mantendo transparência, expandindo o canvas."""
    h, w = img.shape[:2]
    cx, cy = w / 2, h / 2

    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)

    # Calcular novo tamanho do canvas
    cos_a = abs(M[0, 0])
    sin_a = abs(M[0, 1])
    new_w = int(h * sin_a + w * cos_a)
    new_h = int(h * cos_a + w * sin_a)

    # Ajustar translação
    M[0, 2] += (new_w / 2) - cx
    M[1, 2] += (new_h / 2) - cy

    rotated = cv2.warpAffine(
        img, M, (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    return rotated


# ──────────────────────────────────────────────
# Composição: colar carta sobre background
# ──────────────────────────────────────────────
def paste_card_on_bg(
    bg: np.ndarray,
    card_bgra: np.ndarray,
    x: int,
    y: int,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """
    Cola carta BGRA sobre background BGR na posição (x, y).
    Retorna (bg_modificado, bbox_xyxy) onde bbox é a bounding box real (clipped).
    """
    bg_h, bg_w = bg.shape[:2]
    c_h, c_w = card_bgra.shape[:2]

    # Calcular regiões de overlap com os limites da imagem
    x1 = max(x, 0)
    y1 = max(y, 0)
    x2 = min(x + c_w, bg_w)
    y2 = min(y + c_h, bg_h)

    if x2 <= x1 or y2 <= y1:
        return bg, (0, 0, 0, 0)

    # Recorte correspondente na carta
    cx1 = x1 - x
    cy1 = y1 - y
    cx2 = cx1 + (x2 - x1)
    cy2 = cy1 + (y2 - y1)

    card_region = card_bgra[cy1:cy2, cx1:cx2]
    alpha = card_region[:, :, 3:4].astype(np.float32) / 255.0
    card_bgr = card_region[:, :, :3].astype(np.float32)
    bg_region = bg[y1:y2, x1:x2].astype(np.float32)

    # Alpha blending
    blended = card_bgr * alpha + bg_region * (1.0 - alpha)
    bg[y1:y2, x1:x2] = blended.astype(np.uint8)

    # Calcular tight bounding box baseada nos pixels com alpha > 0
    alpha_mask = card_region[:, :, 3] > 10  # threshold para evitar bordas suaves
    if not alpha_mask.any():
        return bg, (0, 0, 0, 0)

    rows = np.any(alpha_mask, axis=1)
    cols = np.any(alpha_mask, axis=0)
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]

    # Converter para coordenadas absolutas no background
    abs_x1 = x1 + cmin
    abs_y1 = y1 + rmin
    abs_x2 = x1 + cmax
    abs_y2 = y1 + rmax

    return bg, (int(abs_x1), int(abs_y1), int(abs_x2), int(abs_y2))


# ──────────────────────────────────────────────
# Cálculo de IoU para evitar sobreposição total
# ──────────────────────────────────────────────
def compute_iou(box_a: tuple, box_b: tuple) -> float:
    """Calcula IoU entre duas bboxes (x1, y1, x2, y2)."""
    xa = max(box_a[0], box_b[0])
    ya = max(box_a[1], box_b[1])
    xb = min(box_a[2], box_b[2])
    yb = min(box_a[3], box_b[3])

    inter = max(0, xb - xa) * max(0, yb - ya)
    if inter == 0:
        return 0.0

    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - inter

    return inter / union if union > 0 else 0.0


def bbox_xyxy_to_yolo(bbox: tuple, img_w: int, img_h: int) -> tuple[float, float, float, float]:
    """Converte bbox (x1, y1, x2, y2) para YOLO (x_center, y_center, w, h) normalizado."""
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    x_center = (x1 + x2) / 2.0 / img_w
    y_center = (y1 + y2) / 2.0 / img_h
    w_norm = w / img_w
    h_norm = h / img_h
    return (
        round(x_center, 6),
        round(y_center, 6),
        round(w_norm, 6),
        round(h_norm, 6),
    )


# ──────────────────────────────────────────────
# Simulação de mão do jogador (fan/row)
# ──────────────────────────────────────────────
def generate_hand_cards(
    bg: np.ndarray,
    cards: list[tuple[str, np.ndarray]],
    imgsz: int,
    hand_cards_min: int,
    hand_cards_max: int,
    hand_scale_min: float,
    hand_scale_max: float,
    brightness_range: float,
    contrast_range: float,
) -> tuple[np.ndarray, list[tuple[int, float, float, float, float]]]:
    """
    Simula as cartas na mão do jogador: em leque/fileira na parte inferior da tela.
    Cartas menores, sobrepostas parcialmente, com leve rotação em leque.
    """
    num_cards = random.randint(hand_cards_min, hand_cards_max)
    selected = random.sample(cards, min(num_cards, len(cards)))

    labels: list[tuple[int, float, float, float, float]] = []

    # Tamanho consistente para todas cartas na mão
    card_scale = random.uniform(hand_scale_min, hand_scale_max)
    card_h = int(imgsz * card_scale)
    card_h = max(card_h, 25)

    # Posição Y: parte inferior (70%-90% da imagem)
    base_y = int(imgsz * random.uniform(0.68, 0.88))

    # Posição X: centrado horizontalmente
    # Calcular largura da carta para estimar o spread
    sample_card = selected[0][1]
    aspect = sample_card.shape[1] / sample_card.shape[0]
    est_card_w = int(card_h * aspect)

    # Overlap: cards se sobrepõem 30-60% horizontalmente
    overlap_frac = random.uniform(0.30, 0.60)
    step_x = int(est_card_w * (1.0 - overlap_frac))
    step_x = max(step_x, 10)

    total_w = step_x * (num_cards - 1) + est_card_w
    start_x = (imgsz - total_w) // 2
    # Adicionar jitter horizontal
    start_x += random.randint(-int(imgsz * 0.1), int(imgsz * 0.1))
    start_x = max(5, min(start_x, imgsz - total_w - 5))

    # Fan rotation: cartas do lado esquerdo inclinam para esquerda, direita para direita
    max_fan_angle = random.uniform(3.0, 12.0)

    for i, (card_name, card_img) in enumerate(selected):
        class_id = CLASS_MAP[card_name]

        # Rotação em leque
        if num_cards > 1:
            t = (i / (num_cards - 1)) - 0.5  # -0.5 a +0.5
            fan_angle = t * max_fan_angle * 2
        else:
            fan_angle = 0

        # Augmentar carta
        aug_card = augment_card(
            card_img, card_h, abs(fan_angle) + 2.0, brightness_range, contrast_range
        )
        # Aplicar rotação do leque separadamente
        if abs(fan_angle) > 0.5:
            aug_card = rotate_bgra(aug_card, fan_angle)

        ac_h, ac_w = aug_card.shape[:2]

        # Posição
        px = start_x + i * step_x
        # Leve variação vertical (arco da mão)
        arc = int(abs(i - (num_cards - 1) / 2) * 3)  # Centro mais baixo
        py = base_y - arc + random.randint(-3, 3)

        # Colar na imagem
        bg, bbox = paste_card_on_bg(bg, aug_card, px, py)

        bx1, by1, bx2, by2 = bbox
        if bx2 - bx1 < 3 or by2 - by1 < 3:
            continue

        yolo_bbox = bbox_xyxy_to_yolo(bbox, imgsz, imgsz)
        xc, yc, wn, hn = yolo_bbox
        if 0 < xc < 1 and 0 < yc < 1 and wn > 0.005 and hn > 0.005:
            labels.append((class_id, xc, yc, wn, hn))

    return bg, labels


# ──────────────────────────────────────────────
# Geração de uma imagem sintética
# ──────────────────────────────────────────────
def generate_one_image(
    backgrounds: list[np.ndarray],
    cards: list[tuple[str, np.ndarray]],
    imgsz: int,
    cards_min: int,
    cards_max: int,
    card_scale_min: float,
    card_scale_max: float,
    rotation_max: float,
    max_overlap: float,
    brightness_range: float,
    contrast_range: float,
    mode: str = "board",
    hand_cards_min: int = 2,
    hand_cards_max: int = 6,
    hand_scale_min: float = 0.06,
    hand_scale_max: float = 0.12,
) -> tuple[np.ndarray, list[tuple[int, float, float, float, float]]]:
    """
    Gera uma única imagem sintética com cartas coladas sobre um background.
    
    mode:
        "board" — cartas espalhadas no centro (board / mesa)
        "hand"  — cartas em leque na parte inferior (mão do jogador)
        "mixed" — tanto cartas de board quanto mão do jogador
    
    Retorna (imagem_BGR, lista_de_labels_yolo).
    Cada label: (class_id, x_center, y_center, w, h)
    """
    # Escolher e preparar background
    bg_orig = random.choice(backgrounds)
    bg = cv2.resize(bg_orig, (imgsz, imgsz), interpolation=cv2.INTER_AREA)

    # Aplicar augmentation leve no background
    bg = augment_background(bg, brightness_range * 0.5, contrast_range * 0.5)

    all_labels: list[tuple[int, float, float, float, float]] = []

    if mode == "hand":
        # Somente cartas na mão
        bg, hand_labels = generate_hand_cards(
            bg, cards, imgsz,
            hand_cards_min, hand_cards_max,
            hand_scale_min, hand_scale_max,
            brightness_range, contrast_range,
        )
        all_labels.extend(hand_labels)
        return bg, all_labels

    # ── Board cards (modo "board" ou a parte board do "mixed") ──
    num_cards = random.randint(cards_min, cards_max)
    selected_cards = random.sample(cards, min(num_cards, len(cards)))

    labels: list[tuple[int, float, float, float, float]] = []
    placed_boxes: list[tuple[int, int, int, int]] = []

    # Zona de posicionamento: região central (20%-80% do canvas)
    margin_x = int(imgsz * 0.15)
    margin_y = int(imgsz * 0.20)
    zone_x1 = margin_x
    zone_y1 = margin_y
    zone_x2 = imgsz - margin_x
    zone_y2 = imgsz - margin_y

    for card_name, card_img in selected_cards:
        class_id = CLASS_MAP[card_name]

        # Escala aleatória da carta
        card_h = int(imgsz * random.uniform(card_scale_min, card_scale_max))
        card_h = max(card_h, 20)  # mínimo de 20px

        # Augmentar carta (resize + rotação + cor)
        aug_card = augment_card(
            card_img, card_h, rotation_max, brightness_range, contrast_range
        )

        ac_h, ac_w = aug_card.shape[:2]

        # Tentar posicionar sem overlap excessivo (max 50 tentativas)
        placed = False
        px = random.randint(zone_x1, max(zone_x1, zone_x2 - ac_w))
        py = random.randint(zone_y1, max(zone_y1, zone_y2 - ac_h))
        for _ in range(50):
            px = random.randint(zone_x1, max(zone_x1, zone_x2 - ac_w))
            py = random.randint(zone_y1, max(zone_y1, zone_y2 - ac_h))

            candidate_box = (px, py, px + ac_w, py + ac_h)

            # Verificar overlap com cartas já posicionadas
            overlap_ok = True
            for existing_box in placed_boxes:
                iou = compute_iou(candidate_box, existing_box)
                if iou > max_overlap:
                    overlap_ok = False
                    break

            if overlap_ok:
                placed = True
                break

        if not placed:
            # Posicionar mesmo assim, mas com overlap
            px = random.randint(zone_x1, max(zone_x1, zone_x2 - ac_w))
            py = random.randint(zone_y1, max(zone_y1, zone_y2 - ac_h))

        # Colar carta no background
        bg, bbox = paste_card_on_bg(bg, aug_card, px, py)

        # Validar bbox
        bx1, by1, bx2, by2 = bbox
        if bx2 - bx1 < 3 or by2 - by1 < 3:
            continue  # carta muito pequena ou fora do canvas

        placed_boxes.append(bbox)

        # Converter para formato YOLO
        yolo_bbox = bbox_xyxy_to_yolo(bbox, imgsz, imgsz)
        xc, yc, wn, hn = yolo_bbox

        # Sanity: valores devem estar em (0, 1)
        if 0 < xc < 1 and 0 < yc < 1 and wn > 0.005 and hn > 0.005:
            labels.append((class_id, xc, yc, wn, hn))

    all_labels.extend(labels)

    # ── Se modo "mixed", adicionar cartas de mão ──
    if mode == "mixed":
        # Usar cartas diferentes das já usadas no board
        used_names = {name for name, _ in selected_cards}
        remaining = [(n, img) for n, img in cards if n not in used_names]
        if len(remaining) >= hand_cards_min:
            bg, hand_labels = generate_hand_cards(
                bg, remaining, imgsz,
                hand_cards_min, hand_cards_max,
                hand_scale_min, hand_scale_max,
                brightness_range, contrast_range,
            )
            all_labels.extend(hand_labels)

    return bg, all_labels


def augment_background(bg: np.ndarray, brightness_range: float, contrast_range: float) -> np.ndarray:
    """Aplica variação leve de brilho/contraste no background."""
    img = bg.astype(np.float32)
    brightness = 1.0 + random.uniform(-brightness_range, brightness_range)
    contrast = 1.0 + random.uniform(-contrast_range, contrast_range)
    mean = img.mean()
    img = (img - mean) * contrast + mean
    img = img * brightness
    return np.clip(img, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────
# Pipeline principal
# ──────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)

    # ── Resolver caminhos ──
    cards_dir = Path(args.assets_cards)
    if not cards_dir.is_absolute():
        cards_dir = PROJECT_ROOT / cards_dir

    bg_dir = Path(args.assets_bg)
    if not bg_dir.is_absolute():
        bg_dir = PROJECT_ROOT / bg_dir

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    # ── Validar pastas de entrada ──
    if not cards_dir.exists():
        print(f"[ERRO] Pasta de cartas não encontrada: {cards_dir}")
        print(f"  Crie a pasta e coloque os PNGs das 52 cartas (ex: Ah.png, 2c.png)")
        sys.exit(1)

    if not bg_dir.exists():
        print(f"[ERRO] Pasta de backgrounds não encontrada: {bg_dir}")
        print(f"  Crie a pasta e coloque prints de mesas vazias")
        sys.exit(1)

    # ── Carregar assets ──
    print("[INFO] Carregando assets...")
    cards = load_card_assets(cards_dir)
    backgrounds = load_backgrounds(bg_dir)

    if not cards:
        print(f"[ERRO] Nenhuma carta encontrada em: {cards_dir}")
        sys.exit(1)

    if not backgrounds:
        print(f"[ERRO] Nenhum background encontrado em: {bg_dir}")
        sys.exit(1)

    print(f"  Cartas carregadas: {len(cards)}/52")
    print(f"  Backgrounds carregados: {len(backgrounds)}")

    # Listar cartas faltantes
    loaded_names = {name for name, _ in cards}
    missing = set(CLASS_MAP.keys()) - loaded_names
    if missing:
        print(f"  [WARN] Cartas faltando ({len(missing)}): {sorted(missing)}")

    # ── Criar pastas de saída ──
    num_val = int(args.num_images * args.split_val) if args.split_val > 0 else 0
    num_train = args.num_images - num_val

    splits: list[tuple[str, int]] = [("train", num_train)]
    if num_val > 0:
        splits.append(("val", num_val))

    for split_name, _ in splits:
        (output_dir / "images" / split_name).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split_name).mkdir(parents=True, exist_ok=True)

    # ── Gerar imagens ──
    print(f"\n[INFO] Gerando {args.num_images} imagens sintéticas...")
    print(f"  Train: {num_train} | Val: {num_val}")
    print(f"  Cartas por imagem (board): {args.cards_min}-{args.cards_max}")
    print(f"  Cartas na mão: {args.hand_cards_min}-{args.hand_cards_max}")
    print(f"  Tamanho: {args.imgsz}x{args.imgsz}")
    print(f"  Escala board: {args.card_scale_min:.0%}-{args.card_scale_max:.0%}")
    print(f"  Escala mão: {args.hand_scale_min:.0%}-{args.hand_scale_max:.0%}")
    print(f"  Rotação máx: ±{args.rotation_max}°")
    print(f"  Max overlap (IoU): {args.max_overlap}")
    print(f"  Proporção mão: {args.hand_ratio:.0%} (hand+mixed)")
    print()

    total_labels = 0
    img_idx = 0

    for split_name, split_count in splits:
        img_dir = output_dir / "images" / split_name
        lbl_dir = output_dir / "labels" / split_name

        desc = f"Gerando {split_name}"
        for i in tqdm(range(split_count), desc=desc, unit="img"):
            # Escolher modo: board, hand ou mixed
            r = random.random()
            hand_ratio = args.hand_ratio
            if r < hand_ratio * 0.4:
                mode = "hand"      # ~16% só mão
            elif r < hand_ratio:
                mode = "mixed"     # ~24% board + mão
            else:
                mode = "board"     # ~60% só board

            image, labels = generate_one_image(
                backgrounds=backgrounds,
                cards=cards,
                imgsz=args.imgsz,
                cards_min=args.cards_min,
                cards_max=args.cards_max,
                card_scale_min=args.card_scale_min,
                card_scale_max=args.card_scale_max,
                rotation_max=args.rotation_max,
                max_overlap=args.max_overlap,
                brightness_range=args.brightness_range,
                contrast_range=args.contrast_range,
                mode=mode,
                hand_cards_min=args.hand_cards_min,
                hand_cards_max=args.hand_cards_max,
                hand_scale_min=args.hand_scale_min,
                hand_scale_max=args.hand_scale_max,
            )

            # Salvar imagem
            img_name = f"synth_{img_idx:05d}.jpg"
            cv2.imwrite(str(img_dir / img_name), image, [cv2.IMWRITE_JPEG_QUALITY, 95])

            # Salvar labels
            lbl_name = f"synth_{img_idx:05d}.txt"
            with open(lbl_dir / lbl_name, "w") as f:
                for class_id, xc, yc, wn, hn in labels:
                    f.write(f"{class_id} {xc} {yc} {wn} {hn}\n")

            total_labels += len(labels)
            img_idx += 1

    # ── Gerar classes.txt ──
    classes_file = output_dir / "classes.txt"
    sorted_classes = sorted(FULL_CLASS_MAP.items(), key=lambda x: x[1])
    with open(classes_file, "w") as f:
        for name, idx in sorted_classes:
            f.write(f"{idx}: {name}\n")

    # ── Gerar data.yaml para o dataset sintético ──
    data_yaml = output_dir / "data.yaml"
    with open(data_yaml, "w") as f:
        f.write("# Dataset sintético gerado por generate_synthetic_data.py\n")
        f.write(f"path: {output_dir.as_posix()}\n")
        f.write("train: images/train\n")
        if num_val > 0:
            f.write("val: images/val\n")
        f.write(f"\nnc: {len(FULL_CLASS_MAP)}\n\nnames:\n")
        for name, idx in sorted_classes:
            f.write(f"  {idx}: {name}\n")

    # ── Resumo final ──
    print(f"\n{'='*50}")
    print(f"[OK] Geração concluída!")
    print(f"  Imagens geradas: {img_idx}")
    print(f"  Labels totais:   {total_labels}")
    print(f"  Média labels/img: {total_labels / max(img_idx, 1):.1f}")
    print(f"  Saída:           {output_dir}")
    print(f"  classes.txt:     {classes_file}")
    print(f"  data.yaml:       {data_yaml}")
    print(f"{'='*50}")
    print(f"\nPara treinar com os dados sintéticos:")
    print(f"  python training/train_yolo.py --data {data_yaml} --epochs 100")
    print(f"\nPara combinar com dados reais, edite o data.yaml ou faça merge das pastas.")


if __name__ == "__main__":
    main()

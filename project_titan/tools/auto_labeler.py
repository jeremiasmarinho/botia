"""
Project Titan — Auto-Labeler v0

Gera labels YOLO aproximados para regiões conhecidas (pot, stack, botões)
a partir das coordenadas calibradas no config YAML.

Não tenta detectar cartas individualmente — essas precisam de anotação
manual — mas pré-popula as classes 52-57 (botões + pot + stack) com
bounding boxes derivadas das coordenadas físicas na referência de ecrã.

Uso:
    python -m tools.auto_labeler
    python -m tools.auto_labeler --config config_club.yaml
    python -m tools.auto_labeler --source data/to_annotate --dry-run
    python -m tools.auto_labeler --btn-size 80 40
    python -m tools.auto_labeler --hero-class-id 56   (override de classe)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Class IDs (must match training/data.yaml) ────────────────
CLASS_FOLD = 52
CLASS_CHECK = 53
CLASS_RAISE = 54
CLASS_RAISE_2X = 55
CLASS_RAISE_2_5X = 56
CLASS_RAISE_POT = 57
CLASS_RAISE_CONFIRM = 58
CLASS_ALLIN = 59
CLASS_POT = 60
CLASS_STACK = 61

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Default button bbox size (pixels) when only center is known
DEFAULT_BTN_WIDTH = 90
DEFAULT_BTN_HEIGHT = 44


def _load_config(config_path: Path) -> dict:
    """Load YAML config file."""
    try:
        import yaml
    except ImportError:
        print("[AUTO-LABEL] ERRO: pyyaml não instalado (pip install pyyaml)")
        sys.exit(1)

    if not config_path.exists():
        print(f"[AUTO-LABEL] ERRO: config não encontrado: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_image_dimensions(image_path: Path) -> tuple[int, int] | None:
    """Get (width, height) of an image without loading full frame."""
    try:
        import cv2
        img = cv2.imread(str(image_path))
        if img is not None:
            h, w = img.shape[:2]
            return (w, h)
    except ImportError:
        pass

    # Fallback: try PIL
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            return im.size  # (width, height)
    except ImportError:
        pass

    return None


def _pixel_to_yolo(
    px: int, py: int, pw: int, ph: int,
    img_w: int, img_h: int,
) -> tuple[float, float, float, float]:
    """Convert pixel bbox (x, y, w, h) to YOLO normalized (cx, cy, w, h).

    Args:
        px, py: top-left corner in pixels
        pw, ph: width and height in pixels
        img_w, img_h: image dimensions

    Returns:
        (center_x, center_y, width, height) all in [0, 1]
    """
    cx = (px + pw / 2.0) / img_w
    cy = (py + ph / 2.0) / img_h
    nw = pw / img_w
    nh = ph / img_h

    # Clamp to [0, 1]
    cx = max(0.0, min(1.0, cx))
    cy = max(0.0, min(1.0, cy))
    nw = max(0.0, min(1.0, nw))
    nh = max(0.0, min(1.0, nh))

    return (cx, cy, nw, nh)


def _center_to_bbox(
    center_x: int, center_y: int,
    bbox_w: int, bbox_h: int,
) -> tuple[int, int, int, int]:
    """Convert center point to top-left + size bbox."""
    x = center_x - bbox_w // 2
    y = center_y - bbox_h // 2
    return (x, y, bbox_w, bbox_h)


def _extract_regions_from_config(
    cfg: dict,
    btn_w: int,
    btn_h: int,
) -> list[tuple[int, int, int, int, int]]:
    """Extract known regions from config as (class_id, x, y, w, h) pixel coords.

    Returns list of (class_id, px, py, pw, ph).
    """
    regions: list[tuple[int, int, int, int, int]] = []

    # ── Pot region ──
    # Try ocr.pot_box first (structured), then ocr.pot_region (comma-separated)
    ocr = cfg.get("ocr", {})
    pot_box = ocr.get("pot_box", {})
    if pot_box and all(k in pot_box for k in ("x", "y", "w", "h")):
        regions.append((CLASS_POT, int(pot_box["x"]), int(pot_box["y"]),
                        int(pot_box["w"]), int(pot_box["h"])))
    elif "pot_region" in ocr:
        parts = str(ocr["pot_region"]).split(",")
        if len(parts) == 4:
            px, py, pw, ph = (int(p.strip()) for p in parts)
            regions.append((CLASS_POT, px, py, pw, ph))

    # ── Stack region ──
    if "stack_region" in ocr:
        parts = str(ocr["stack_region"]).split(",")
        if len(parts) == 4:
            sx, sy, sw, sh = (int(p.strip()) for p in parts)
            regions.append((CLASS_STACK, sx, sy, sw, sh))

    # ── Action buttons (center coords → bbox) ──
    action_coords = cfg.get("action_coordinates", {})
    btn_map = {
        "fold": CLASS_FOLD,
        "call": CLASS_CHECK,
        "raise": CLASS_RAISE,
    }
    for key, class_id in btn_map.items():
        coord = action_coords.get(key, {})
        if isinstance(coord, dict) and "x" in coord and "y" in coord:
            cx, cy = int(coord["x"]), int(coord["y"])
            bx, by, bw, bh = _center_to_bbox(cx, cy, btn_w, btn_h)
            regions.append((class_id, bx, by, bw, bh))

    # Fallback: try action_buttons (list format)
    if not action_coords:
        action_buttons = cfg.get("action_buttons", {})
        btn_map_list = {
            "fold": CLASS_FOLD,
            "call": CLASS_CHECK,
            "raise": CLASS_RAISE,
            "raise_2x": CLASS_RAISE_2X,
            "raise_2_5x": CLASS_RAISE_2_5X,
            "raise_pot": CLASS_RAISE_POT,
            "raise_confirm": CLASS_RAISE_CONFIRM,
        }
        for key, class_id in btn_map_list.items():
            val = action_buttons.get(key)
            if isinstance(val, list) and len(val) >= 2:
                cx, cy = int(val[0]), int(val[1])
                bx, by, bw, bh = _center_to_bbox(cx, cy, btn_w, btn_h)
                regions.append((class_id, bx, by, bw, bh))

    # ── Hero area (for reference — marked as comment, not a YOLO class) ──
    # vision.regions.hero_area and board_area are useful but don't map to
    # individual card classes (0-51), so we skip them in auto-labeling.

    return regions


def _generate_labels(
    source_dir: Path,
    cfg: dict,
    btn_w: int,
    btn_h: int,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict:
    """Generate YOLO label files for all images in source_dir.

    Returns summary dict.
    """
    regions = _extract_regions_from_config(cfg, btn_w, btn_h)

    if not regions:
        print("[AUTO-LABEL] Nenhuma região encontrada no config.")
        return {"images": 0, "labels_written": 0, "regions_per_image": 0}

    images = sorted(
        f for f in source_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not images:
        print(f"[AUTO-LABEL] Nenhuma imagem em {source_dir}")
        return {"images": 0, "labels_written": 0, "regions_per_image": 0}

    # Get reference dimensions from first image
    ref_dims = _get_image_dimensions(images[0])
    if ref_dims is None:
        print("[AUTO-LABEL] ERRO: impossível ler dimensões da imagem "
              "(instale opencv-python ou Pillow)")
        sys.exit(1)

    img_w, img_h = ref_dims
    print(f"[AUTO-LABEL] Resolução de referência: {img_w}x{img_h}")

    # Convert pixel regions to YOLO format
    yolo_lines: list[str] = []
    for class_id, px, py, pw, ph in regions:
        cx, cy, nw, nh = _pixel_to_yolo(px, py, pw, ph, img_w, img_h)
        yolo_lines.append(f"{class_id} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    label_block = "\n".join(yolo_lines)
    labels_written = 0

    print(f"\n[AUTO-LABEL] Regiões detectadas ({len(regions)}):")
    for class_id, px, py, pw, ph in regions:
        from tools.label_assist import ALL_CLASS_NAMES
        name = ALL_CLASS_NAMES[class_id] if class_id < len(ALL_CLASS_NAMES) else f"id_{class_id}"
        print(f"  [{class_id:2d}] {name:20s}  pixel=({px},{py},{pw},{ph})")

    if dry_run:
        print(f"\n[AUTO-LABEL] DRY-RUN — {len(images)} imagens, "
              f"{len(regions)} regiões cada")
        print(f"\n  Exemplo de label gerado:")
        for line in yolo_lines:
            print(f"    {line}")
        return {
            "images": len(images),
            "labels_written": 0,
            "regions_per_image": len(regions),
            "dry_run": True,
        }

    for img_path in images:
        label_path = img_path.with_suffix(".txt")

        # Check if image has different dimensions
        dims = _get_image_dimensions(img_path)
        if dims and dims != (img_w, img_h):
            # Recalculate for this specific image
            iw, ih = dims
            lines = []
            for class_id, px, py, pw, ph in regions:
                cx, cy, nw, nh = _pixel_to_yolo(px, py, pw, ph, iw, ih)
                lines.append(f"{class_id} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
            content = "\n".join(lines)
        else:
            content = label_block

        if label_path.exists() and not overwrite:
            # Append to existing label (don't duplicate)
            existing = label_path.read_text(encoding="utf-8").strip()
            existing_classes = set()
            for line in existing.splitlines():
                parts = line.strip().split()
                if parts:
                    try:
                        existing_classes.add(int(parts[0]))
                    except ValueError:
                        pass

            # Only add lines for classes not already present
            new_lines = []
            for line in content.splitlines():
                parts = line.strip().split()
                if parts:
                    try:
                        cid = int(parts[0])
                        if cid not in existing_classes:
                            new_lines.append(line)
                    except ValueError:
                        new_lines.append(line)

            if new_lines:
                merged = existing + "\n" + "\n".join(new_lines) if existing else "\n".join(new_lines)
                label_path.write_text(merged.strip() + "\n", encoding="utf-8")
                labels_written += 1
        else:
            label_path.write_text(content.strip() + "\n", encoding="utf-8")
            labels_written += 1

    summary = {
        "images": len(images),
        "labels_written": labels_written,
        "regions_per_image": len(regions),
        "reference_resolution": f"{img_w}x{img_h}",
    }

    print(f"\n[AUTO-LABEL] === Resultado ===")
    print(f"  Imagens:          {len(images)}")
    print(f"  Labels escritos:  {labels_written}")
    print(f"  Regiões/imagem:   {len(regions)}")
    print(f"  Resolução ref:    {img_w}x{img_h}")

    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-label v0 — generate YOLO labels from calibrated regions"
    )
    parser.add_argument(
        "--config", type=str, default="config_club.yaml",
        help="Config YAML with calibrated regions (default: config_club.yaml)",
    )
    parser.add_argument(
        "--source", type=str, default="data/to_annotate",
        help="Dir with images to label (default: data/to_annotate)",
    )
    parser.add_argument(
        "--btn-size", type=int, nargs=2, default=[DEFAULT_BTN_WIDTH, DEFAULT_BTN_HEIGHT],
        metavar=("W", "H"),
        help=f"Button bbox size in pixels (default: {DEFAULT_BTN_WIDTH} {DEFAULT_BTN_HEIGHT})",
    )
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Preview without writing files")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing label files completely")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path

    source_dir = Path(args.source)
    if not source_dir.is_absolute():
        source_dir = PROJECT_ROOT / source_dir

    if not source_dir.exists():
        print(f"[AUTO-LABEL] ERRO: diretório source não encontrado: {source_dir}")
        print(f"[AUTO-LABEL] Dica: execute primeiro 'python -m tools.label_assist' "
              f"para preparar as imagens")
        sys.exit(1)

    cfg = _load_config(config_path)

    _generate_labels(
        source_dir=source_dir,
        cfg=cfg,
        btn_w=args.btn_size[0],
        btn_h=args.btn_size[1],
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()

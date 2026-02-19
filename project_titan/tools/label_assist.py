"""
Project Titan — Label Assist Tool

Prepara frames raw (data/raw/) para anotação no formato YOLO.

Funcionalidades:
  - Copia PNGs de data/raw/ para uma pasta de trabalho (data/to_annotate/)
  - Gera ficheiros .txt vazios (placeholder) para cada imagem
  - Opcionalmente redimensiona imagens para resolução YOLO (640x640)
  - Gera classes.txt compatível com data.yaml (58 classes)
  - Exporta manifest CSV com lista de imagens para controlo de progresso
  - Filtra imagens duplicadas por hash MD5

Uso:
    python -m tools.label_assist
    python -m tools.label_assist --source data/raw --output data/to_annotate
    python -m tools.label_assist --resize 640
    python -m tools.label_assist --export-classes classes.txt
    python -m tools.label_assist --stats
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Class list (must match training/data.yaml exactly) ────────
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
SUITS = ["c", "d", "h", "s"]
CARD_NAMES = [f"{r}{s}" for r in RANKS for s in SUITS]
ACTION_NAMES = [
    "fold", "check", "raise", "raise_2x",
    "raise_2_5x", "raise_pot", "raise_confirm", "allin",
]
REGION_NAMES = ["pot", "stack"]
ALL_CLASS_NAMES = CARD_NAMES + ACTION_NAMES + REGION_NAMES  # 62 classes

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _md5(filepath: Path) -> str:
    """Compute MD5 hash of a file."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _export_classes(output_path: Path) -> None:
    """Write classes.txt with one class name per line (index = line number)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(ALL_CLASS_NAMES) + "\n", encoding="utf-8")
    print(f"[LABEL] classes.txt exportado ({len(ALL_CLASS_NAMES)} classes): {output_path}")


def _prepare_for_annotation(
    source_dir: Path,
    output_dir: Path,
    resize: int | None = None,
    dedup: bool = True,
) -> dict:
    """Copy raw images to annotation workspace, create empty label stubs.

    Returns:
        Summary dict with counts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find source images
    source_images = sorted(
        f for f in source_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not source_images:
        print(f"[LABEL] Nenhuma imagem encontrada em {source_dir}")
        return {"total": 0, "copied": 0, "skipped_dup": 0}

    # Dedup by hash
    seen_hashes: set[str] = set()
    copied = 0
    skipped_dup = 0
    skipped_existing = 0
    manifest_rows: list[dict] = []

    # Optional resize
    cv2 = None
    if resize:
        try:
            import cv2 as _cv2
            cv2 = _cv2
        except ImportError:
            print("[LABEL] AVISO: opencv-python não disponível, resize desativado")
            resize = None

    for img_path in source_images:
        if dedup:
            file_hash = _md5(img_path)
            if file_hash in seen_hashes:
                skipped_dup += 1
                continue
            seen_hashes.add(file_hash)

        dest_img = output_dir / img_path.name
        dest_label = output_dir / (img_path.stem + ".txt")

        if dest_img.exists():
            skipped_existing += 1
            continue

        if resize and cv2 is not None:
            frame = cv2.imread(str(img_path))
            if frame is not None:
                frame = cv2.resize(frame, (resize, resize))
                cv2.imwrite(str(dest_img), frame)
            else:
                shutil.copy2(img_path, dest_img)
        else:
            shutil.copy2(img_path, dest_img)

        # Create empty label stub (if not already annotated)
        if not dest_label.exists():
            dest_label.write_text("", encoding="utf-8")

        copied += 1
        manifest_rows.append({
            "filename": img_path.name,
            "annotated": "no",
            "source": str(img_path),
        })

    # Write manifest CSV
    manifest_path = output_dir / "manifest.csv"
    write_header = not manifest_path.exists()
    with open(manifest_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "annotated", "source"])
        if write_header:
            writer.writeheader()
        writer.writerows(manifest_rows)

    # Write classes.txt alongside images
    _export_classes(output_dir / "classes.txt")

    summary = {
        "total_source": len(source_images),
        "copied": copied,
        "skipped_dup": skipped_dup,
        "skipped_existing": skipped_existing,
        "output_dir": str(output_dir),
        "resize": resize,
    }

    print(f"[LABEL] === Preparação concluída ===")
    print(f"  Source:           {source_dir} ({len(source_images)} imagens)")
    print(f"  Output:           {output_dir}")
    print(f"  Copiadas:         {copied}")
    print(f"  Duplicatas:       {skipped_dup}")
    print(f"  Já existentes:    {skipped_existing}")
    if resize:
        print(f"  Resize:           {resize}x{resize}")
    print(f"  Manifest:         {manifest_path}")
    print(f"  Classes:          {output_dir / 'classes.txt'} ({len(ALL_CLASS_NAMES)})")

    return summary


def _show_stats(annotation_dir: Path) -> None:
    """Show annotation progress statistics."""
    if not annotation_dir.exists():
        print(f"[LABEL] Diretório não encontrado: {annotation_dir}")
        return

    images = [f for f in annotation_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS]
    labels = [f for f in annotation_dir.iterdir() if f.suffix == ".txt" and f.name != "classes.txt"]

    total_images = len(images)
    total_labels = len(labels)
    annotated = 0
    empty = 0
    total_boxes = 0

    for lbl in labels:
        content = lbl.read_text(encoding="utf-8").strip()
        if content:
            annotated += 1
            total_boxes += len(content.splitlines())
        else:
            empty += 1

    images_without_labels = set(f.stem for f in images) - set(f.stem for f in labels)

    print(f"\n[LABEL] === Progresso de Anotação ===")
    print(f"  Imagens:          {total_images}")
    print(f"  Labels criados:   {total_labels}")
    print(f"  Anotados:         {annotated}")
    print(f"  Vazios:           {empty}")
    print(f"  Sem label:        {len(images_without_labels)}")
    print(f"  Total BBoxes:     {total_boxes}")
    if total_images > 0:
        pct = (annotated / total_images) * 100
        print(f"  Progresso:        {pct:.1f}%")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare raw frames for YOLO annotation")
    parser.add_argument("--source", type=str, default="data/raw", help="Source dir with raw captured frames")
    parser.add_argument("--output", type=str, default="data/to_annotate", help="Output dir for annotation workspace")
    parser.add_argument("--resize", type=int, default=None, help="Resize images to NxN pixels (e.g., 640)")
    parser.add_argument("--no-dedup", action="store_true", dest="no_dedup", help="Skip MD5 deduplication")
    parser.add_argument("--export-classes", type=str, default=None, dest="export_classes", help="Export classes.txt to path")
    parser.add_argument("--stats", action="store_true", help="Show annotation progress stats")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.export_classes:
        path = Path(args.export_classes)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        _export_classes(path)
        return

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    if args.stats:
        _show_stats(output_dir)
        return

    source_dir = Path(args.source)
    if not source_dir.is_absolute():
        source_dir = PROJECT_ROOT / source_dir

    if not source_dir.exists():
        print(f"[LABEL] ERRO: diretório source não encontrado: {source_dir}")
        sys.exit(1)

    _prepare_for_annotation(
        source_dir=source_dir,
        output_dir=output_dir,
        resize=args.resize,
        dedup=not args.no_dedup,
    )


if __name__ == "__main__":
    main()

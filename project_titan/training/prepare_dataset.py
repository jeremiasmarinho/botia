"""
Project Titan — Dataset Preparation Tool

Organiza imagens e labels anotados no formato YOLO para treinamento.

Funcionalidades:
  - Cria estrutura de diretórios (images/train, images/val, images/test, labels/...)
  - Divide dataset em train/val/test por proporção configurável
  - Valida integridade de labels (classes válidas, bboxes normalizadas)
  - Gera relatório de distribuição de classes
  - Suporta importação de diretório flat (images + labels lado a lado)

Uso:
    python training/prepare_dataset.py --source raw_data/ --output datasets/titan_cards --split 0.8 0.15 0.05
    python training/prepare_dataset.py --source raw_data/ --output datasets/titan_cards --validate-only
    python training/prepare_dataset.py --output datasets/titan_cards --stats-only
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Constants ────────────────────────────────────────────────────
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
SUITS = ["c", "d", "h", "s"]
CARD_NAMES = [f"{r}{s}" for r in RANKS for s in SUITS]
ACTION_NAMES = ["btn_fold", "btn_call", "btn_raise_small", "btn_raise_big"]
REGION_NAMES = ["pot", "stack"]
ALL_CLASS_NAMES = CARD_NAMES + ACTION_NAMES + REGION_NAMES

CLASS_NAME_TO_ID: dict[str, int] = {name: idx for idx, name in enumerate(ALL_CLASS_NAMES)}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass
class ValidationIssue:
    file: str
    line: int
    message: str


@dataclass
class DatasetStats:
    total_images: int = 0
    total_labels: int = 0
    images_without_labels: int = 0
    labels_without_images: int = 0
    total_annotations: int = 0
    class_distribution: dict[str, int] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)
    split_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_images": self.total_images,
            "total_labels": self.total_labels,
            "images_without_labels": self.images_without_labels,
            "labels_without_images": self.labels_without_images,
            "total_annotations": self.total_annotations,
            "class_distribution": dict(sorted(self.class_distribution.items(), key=lambda x: x[1], reverse=True)),
            "issues_count": len(self.issues),
            "issues": [{"file": i.file, "line": i.line, "message": i.message} for i in self.issues[:50]],
            "split_counts": self.split_counts,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare dataset for YOLO training")
    parser.add_argument("--source", type=str, default=None, help="Source dir with images + labels (flat)")
    parser.add_argument("--output", type=str, default="datasets/titan_cards", help="Output dataset root")
    parser.add_argument(
        "--split",
        type=float,
        nargs=3,
        default=[0.8, 0.15, 0.05],
        metavar=("TRAIN", "VAL", "TEST"),
        help="Train/Val/Test split ratios (must sum to 1.0)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible splits")
    parser.add_argument("--validate-only", action="store_true", dest="validate_only", help="Validate source without copying")
    parser.add_argument("--stats-only", action="store_true", dest="stats_only", help="Show stats of existing output dataset")
    parser.add_argument("--save-report", type=str, default=None, dest="save_report", help="Save report JSON to path")
    parser.add_argument("--json", action="store_true", help="Output stats as JSON")
    parser.add_argument(
        "--include-unlabeled",
        action="store_true",
        dest="include_unlabeled",
        help="Include images without .txt labels (creates empty stubs = background samples)",
    )
    return parser.parse_args()


def _create_structure(output_root: Path) -> None:
    """Create YOLO dataset directory structure."""
    for split in ("train", "val", "test"):
        (output_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_root / "labels" / split).mkdir(parents=True, exist_ok=True)
    print(f"[DATASET] Estrutura criada em: {output_root}")


def _find_pairs(source: Path, include_unlabeled: bool = False) -> list[tuple[Path, Path]]:
    """Find image-label pairs in a flat directory.

    Args:
        source: directory containing images and optional .txt labels.
        include_unlabeled: when True, create empty .txt stubs for images
            without labels (useful as background/negative samples).
    """
    pairs: list[tuple[Path, Path]] = []
    created_stubs = 0
    image_files = sorted(f for f in source.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS)

    for img in image_files:
        label = img.with_suffix(".txt")
        if label.exists():
            pairs.append((img, label))
        elif include_unlabeled:
            label.write_text("", encoding="utf-8")
            pairs.append((img, label))
            created_stubs += 1

    if created_stubs:
        print(f"[DATASET] Criados {created_stubs} label stubs vazios (background samples)")

    return pairs


def _validate_label_file(label_path: Path, num_classes: int) -> list[ValidationIssue]:
    """Validate a YOLO label file."""
    issues: list[ValidationIssue] = []
    rel = label_path.name

    try:
        lines = label_path.read_text(encoding="utf-8").strip().splitlines()
    except Exception as e:
        issues.append(ValidationIssue(rel, 0, f"Erro ao ler arquivo: {e}"))
        return issues

    for i, line in enumerate(lines, start=1):
        parts = line.strip().split()
        if len(parts) == 0:
            continue

        if len(parts) != 5:
            issues.append(ValidationIssue(rel, i, f"Esperado 5 campos, encontrado {len(parts)}"))
            continue

        try:
            class_id = int(parts[0])
        except ValueError:
            issues.append(ValidationIssue(rel, i, f"class_id inválido: {parts[0]}"))
            continue

        if class_id < 0 or class_id >= num_classes:
            issues.append(ValidationIssue(rel, i, f"class_id fora do range [0, {num_classes - 1}]: {class_id}"))
            continue

        for j, name in enumerate(["cx", "cy", "w", "h"], start=1):
            try:
                val = float(parts[j])
                if val < 0.0 or val > 1.0:
                    issues.append(ValidationIssue(rel, i, f"{name}={val} fora do range [0, 1]"))
            except ValueError:
                issues.append(ValidationIssue(rel, i, f"{name} inválido: {parts[j]}"))

    return issues


def _collect_stats(root: Path, splits: list[str] | None = None) -> DatasetStats:
    """Collect stats from an organized dataset."""
    stats = DatasetStats()

    if splits is None:
        splits = ["train", "val", "test"]

    class_counter: Counter = Counter()

    for split in splits:
        img_dir = root / "images" / split
        lbl_dir = root / "labels" / split

        if not img_dir.exists():
            continue

        images = {f.stem for f in img_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS}
        labels = {f.stem for f in lbl_dir.iterdir() if f.suffix == ".txt"} if lbl_dir.exists() else set()

        split_count = len(images)
        stats.split_counts[split] = split_count
        stats.total_images += split_count
        stats.total_labels += len(labels)
        stats.images_without_labels += len(images - labels)
        stats.labels_without_images += len(labels - images)

        for stem in labels:
            label_path = lbl_dir / f"{stem}.txt"
            issues = _validate_label_file(label_path, len(ALL_CLASS_NAMES))
            stats.issues.extend(issues)

            try:
                for line in label_path.read_text(encoding="utf-8").strip().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 1:
                        try:
                            cid = int(parts[0])
                            if 0 <= cid < len(ALL_CLASS_NAMES):
                                class_counter[ALL_CLASS_NAMES[cid]] += 1
                            stats.total_annotations += 1
                        except ValueError:
                            pass
            except Exception:
                pass

    stats.class_distribution = dict(class_counter)
    return stats


def _split_and_copy(pairs: list[tuple[Path, Path]], output_root: Path, ratios: tuple[float, float, float], seed: int) -> DatasetStats:
    """Split pairs into train/val/test and copy."""
    random.seed(seed)
    shuffled = list(pairs)
    random.shuffle(shuffled)

    n = len(shuffled)
    n_train = int(n * ratios[0])
    n_val = int(n * ratios[1])

    splits_data = {
        "train": shuffled[:n_train],
        "val": shuffled[n_train : n_train + n_val],
        "test": shuffled[n_train + n_val :],
    }

    for split, split_pairs in splits_data.items():
        img_dir = output_root / "images" / split
        lbl_dir = output_root / "labels" / split

        for img, lbl in split_pairs:
            shutil.copy2(img, img_dir / img.name)
            shutil.copy2(lbl, lbl_dir / lbl.name)

    print(f"[DATASET] Split: train={len(splits_data['train'])} val={len(splits_data['val'])} test={len(splits_data['test'])}")
    return _collect_stats(output_root)


def _print_stats(stats: DatasetStats, as_json: bool = False) -> None:
    """Print dataset statistics."""
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **stats.to_dict(),
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n[DATASET] === Estatísticas ===")
        print(f"  Images:         {stats.total_images}")
        print(f"  Labels:         {stats.total_labels}")
        print(f"  Annotations:    {stats.total_annotations}")
        print(f"  Missing labels: {stats.images_without_labels}")
        print(f"  Orphan labels:  {stats.labels_without_images}")
        print(f"  Issues:         {len(stats.issues)}")

        if stats.split_counts:
            print(f"  Splits:         {stats.split_counts}")

        if stats.class_distribution:
            print(f"\n  Top 10 classes:")
            for name, count in sorted(stats.class_distribution.items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"    {name:20s}  {count:>6}")

        if stats.issues:
            print(f"\n  Primeiros problemas ({min(10, len(stats.issues))}):")
            for issue in stats.issues[:10]:
                print(f"    {issue.file}:{issue.line} — {issue.message}")

    return report


def main() -> None:
    args = _parse_args()
    output_root = Path(args.output)
    if not output_root.is_absolute():
        output_root = PROJECT_ROOT / output_root

    if args.stats_only:
        if not output_root.exists():
            print(f"[DATASET] Diretório não encontrado: {output_root}")
            sys.exit(1)
        stats = _collect_stats(output_root)
        report = _print_stats(stats, as_json=args.json)

        if args.save_report:
            rp = Path(args.save_report)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps(report if isinstance(report, dict) else stats.to_dict(), indent=2), encoding="utf-8")
            print(f"[DATASET] report salvo: {rp}")
        return

    if args.source is None:
        print("[DATASET] ERRO: --source é obrigatório (exceto com --stats-only)")
        sys.exit(1)

    source = Path(args.source)
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    if not source.exists():
        print(f"[DATASET] ERRO: diretório source não encontrado: {source}")
        sys.exit(1)

    pairs = _find_pairs(source, include_unlabeled=getattr(args, 'include_unlabeled', False))
    print(f"[DATASET] Encontrados {len(pairs)} pares (image + label) em {source}")

    if len(pairs) == 0:
        print("[DATASET] Nenhum par encontrado. Verifique o diretório source.")
        sys.exit(1)

    if args.validate_only:
        all_issues: list[ValidationIssue] = []
        for _, lbl in pairs:
            all_issues.extend(_validate_label_file(lbl, len(ALL_CLASS_NAMES)))

        if all_issues:
            print(f"[DATASET] {len(all_issues)} problemas encontrados:")
            for issue in all_issues[:20]:
                print(f"  {issue.file}:{issue.line} — {issue.message}")
            sys.exit(1)
        else:
            print(f"[DATASET] Validação OK: {len(pairs)} pares sem problemas.")
        return

    ratio_sum = sum(args.split)
    if abs(ratio_sum - 1.0) > 0.01:
        print(f"[DATASET] ERRO: split ratios devem somar 1.0 (got {ratio_sum})")
        sys.exit(1)

    _create_structure(output_root)
    stats = _split_and_copy(pairs, output_root, tuple(args.split), args.seed)
    report = _print_stats(stats, as_json=args.json)

    if args.save_report:
        rp = Path(args.save_report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report if isinstance(report, dict) else stats.to_dict(), indent=2), encoding="utf-8")
        print(f"[DATASET] report salvo: {rp}")


if __name__ == "__main__":
    main()

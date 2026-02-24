"""
Project Titan — Colab Upload Preparation
=========================================
Empacota datasets para upload ao Google Drive e treinamento no Colab.

Uso:
    python training/prepare_colab_upload.py
    python training/prepare_colab_upload.py --output titan_datasets.zip --include-v2
"""

import os
import sys
import argparse
import zipfile
import shutil
import time
from pathlib import Path
from collections import Counter


def count_class_distribution(label_dirs: list[str]) -> Counter:
    """Conta instâncias por classe nos labels YOLO."""
    counts = Counter()
    for label_dir in label_dirs:
        if not os.path.exists(label_dir):
            continue
        for fname in os.listdir(label_dir):
            if not fname.endswith('.txt'):
                continue
            fpath = os.path.join(label_dir, fname)
            with open(fpath, 'r') as f:
                for line in f:
                    parts = line.strip().split()
                    if parts:
                        cls_id = int(parts[0])
                        counts[cls_id] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description='Prepare datasets for Colab upload')
    parser.add_argument('--output', default='titan_colab_package.zip',
                        help='Output zip filename')
    parser.add_argument('--include-v2', action='store_true',
                        help='Include synthetic_v2 dataset (adds ~356MB)')
    parser.add_argument('--only-labels-analysis', action='store_true',
                        help='Only run class distribution analysis, no packaging')
    parser.add_argument('--max-size-gb', type=float, default=3.0,
                        help='Max package size in GB')
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    # ── Class names ──────────────────────────────────────────
    CLASS_NAMES = {
        0:'2c',1:'2d',2:'2h',3:'2s',4:'3c',5:'3d',6:'3h',7:'3s',
        8:'4c',9:'4d',10:'4h',11:'4s',12:'5c',13:'5d',14:'5h',15:'5s',
        16:'6c',17:'6d',18:'6h',19:'6s',20:'7c',21:'7d',22:'7h',23:'7s',
        24:'8c',25:'8d',26:'8h',27:'8s',28:'9c',29:'9d',30:'9h',31:'9s',
        32:'Tc',33:'Td',34:'Th',35:'Ts',36:'Jc',37:'Jd',38:'Jh',39:'Js',
        40:'Qc',41:'Qd',42:'Qh',43:'Qs',44:'Kc',45:'Kd',46:'Kh',47:'Ks',
        48:'Ac',49:'Ad',50:'Ah',51:'As',
        52:'fold',53:'check',54:'raise',55:'raise_2x',56:'raise_2_5x',
        57:'raise_pot',58:'raise_confirm',59:'allin',60:'pot',61:'stack'
    }

    # ── Datasets to include ──────────────────────────────────
    datasets = [
        ('datasets/synthetic_v3', True),     # PPPoker-realistic (primary)
        ('datasets/synthetic', True),         # Simple cards
        ('datasets/titan_cards', True),       # Real UI captures
    ]
    if args.include_v2:
        datasets.append(('datasets/synthetic_v2', True))

    print('=' * 60)
    print('  Project Titan — Colab Upload Preparation')
    print('=' * 60)

    # ── 1. Class Distribution Analysis ───────────────────────
    print('\n[STEP 1] Class Distribution Analysis')
    all_train_labels = []
    all_val_labels = []
    dataset_stats = []

    for ds_path, include in datasets:
        if not include or not os.path.exists(ds_path):
            continue
        train_labels = os.path.join(ds_path, 'labels', 'train')
        val_labels = os.path.join(ds_path, 'labels', 'val')
        train_imgs = os.path.join(ds_path, 'images', 'train')
        val_imgs = os.path.join(ds_path, 'images', 'val')

        t_count = len(os.listdir(train_imgs)) if os.path.exists(train_imgs) else 0
        v_count = len(os.listdir(val_imgs)) if os.path.exists(val_imgs) else 0

        # Calculate size
        total_size = 0
        for root, dirs, files in os.walk(ds_path):
            for f in files:
                total_size += os.path.getsize(os.path.join(root, f))

        dataset_stats.append({
            'path': ds_path,
            'train': t_count,
            'val': v_count,
            'size_mb': total_size / 1024 / 1024
        })

        if os.path.exists(train_labels):
            all_train_labels.append(train_labels)
        if os.path.exists(val_labels):
            all_val_labels.append(val_labels)

    # Print dataset summary
    total_train = sum(s['train'] for s in dataset_stats)
    total_val = sum(s['val'] for s in dataset_stats)
    total_size_mb = sum(s['size_mb'] for s in dataset_stats)

    print(f'\n  {"Dataset":<35s} {"Train":>6s}  {"Val":>5s}  {"Size":>8s}')
    print(f'  {"-"*35} {"-"*6}  {"-"*5}  {"-"*8}')
    for s in dataset_stats:
        print(f'  {s["path"]:<35s} {s["train"]:>6d}  {s["val"]:>5d}  {s["size_mb"]:>6.0f}MB')
    print(f'  {"TOTAL":<35s} {total_train:>6d}  {total_val:>5d}  {total_size_mb:>6.0f}MB')

    # Class distribution
    train_dist = count_class_distribution(all_train_labels)
    print(f'\n  Classes with instances: {len(train_dist)}/62')

    # Find weak/missing classes
    weak_classes = []
    missing_classes = []
    median_count = sorted(train_dist.values())[len(train_dist) // 2] if train_dist else 0

    for cls_id in range(62):
        count = train_dist.get(cls_id, 0)
        name = CLASS_NAMES.get(cls_id, f'class_{cls_id}')
        if count == 0:
            missing_classes.append((cls_id, name))
        elif count < median_count * 0.3:
            weak_classes.append((cls_id, name, count))

    if missing_classes:
        print(f'\n  ❌ MISSING CLASSES ({len(missing_classes)}):')
        for cls_id, name in missing_classes:
            print(f'      class {cls_id:2d} ({name}) — 0 instances')

    if weak_classes:
        print(f'\n  ⚠️  UNDERREPRESENTED CLASSES ({len(weak_classes)}):')
        for cls_id, name, count in sorted(weak_classes, key=lambda x: x[2]):
            print(f'      class {cls_id:2d} ({name:15s}) — {count:4d} instances (median={median_count})')

    # Action button analysis (critical for poker)
    print(f'\n  🎯 ACTION BUTTON CLASSES (critical):')
    for cls_id in range(52, 62):
        name = CLASS_NAMES.get(cls_id, f'class_{cls_id}')
        count = train_dist.get(cls_id, 0)
        status = '✅' if count >= 100 else '⚠️ ' if count > 0 else '❌'
        print(f'      {status} {name:15s} — {count:4d} instances')

    if args.only_labels_analysis:
        return

    # ── 2. Check size limit ──────────────────────────────────
    print(f'\n[STEP 2] Size Check')
    max_size_mb = args.max_size_gb * 1024
    if total_size_mb > max_size_mb:
        print(f'  ⚠️  Total size ({total_size_mb:.0f}MB) exceeds limit ({max_size_mb:.0f}MB)')
        print(f'      Consider removing synthetic_v2 or reducing dataset')
        print(f'      Use --max-size-gb to adjust limit')

    # ── 3. Package files ─────────────────────────────────────
    print(f'\n[STEP 3] Creating {args.output}')
    output_path = Path(args.output)

    # Copy data.yaml adapted for Colab paths
    colab_data_yaml = """# Project Titan — Colab Dataset Config
# Auto-generated for Google Colab training

path: /content/titan_datasets
train:
  - synthetic_v3/images/train
  - synthetic/images/train
  - titan_cards/images/train
val:
  - synthetic_v3/images/val
  - synthetic/images/val
  - titan_cards/images/val

nc: 62

names:
"""
    for cls_id in range(62):
        colab_data_yaml += f'  {cls_id}: {CLASS_NAMES[cls_id]}\n'

    start_time = time.time()
    file_count = 0

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Add Colab-adapted data.yaml
        zf.writestr('titan_datasets/data.yaml', colab_data_yaml)
        file_count += 1

        # Add datasets
        for ds_path, include in datasets:
            if not include or not os.path.exists(ds_path):
                continue
            ds_name = os.path.basename(ds_path)
            print(f'  📦 Adding {ds_name}...')

            for subdir in ['images/train', 'images/val', 'labels/train', 'labels/val']:
                full_dir = os.path.join(ds_path, subdir)
                if not os.path.exists(full_dir):
                    continue
                for fname in os.listdir(full_dir):
                    fpath = os.path.join(full_dir, fname)
                    if os.path.isfile(fpath):
                        arcname = f'titan_datasets/{ds_name}/{subdir}/{fname}'
                        zf.write(fpath, arcname)
                        file_count += 1

                        if file_count % 1000 == 0:
                            print(f'      {file_count} files...', end='\r')

        # Add model as baseline for transfer learning
        model_path = 'models/titan_v7_hybrid.pt'
        if os.path.exists(model_path):
            zf.write(model_path, 'titan_datasets/titan_v7_hybrid.pt')
            file_count += 1
            print(f'  📦 Added baseline model for transfer learning')

        # Add Colab notebook
        notebook_path = 'training/colab_v8_pro_train.ipynb'
        if os.path.exists(notebook_path):
            zf.write(notebook_path, 'colab_v8_pro_train.ipynb')
            file_count += 1
            print(f'  📦 Added Colab notebook')

    elapsed = time.time() - start_time
    zip_size_mb = os.path.getsize(output_path) / 1024 / 1024

    print(f'\n  ✅ Package created: {output_path}')
    print(f'     Files: {file_count}')
    print(f'     Size:  {zip_size_mb:.0f}MB (compressed)')
    print(f'     Time:  {elapsed:.1f}s')

    # ── 4. Next steps ────────────────────────────────────────
    print(f'\n{"="*60}')
    print(f'  NEXT STEPS FOR COLAB TRAINING')
    print(f'{"="*60}')
    print(f'  1. Upload {output_path} to Google Drive root')
    print(f'  2. Open colab_v8_pro_train.ipynb no Google Colab')
    print(f'  3. Runtime → Change runtime type → GPU (T4 ou melhor)')
    print(f'  4. Execute todas as células em ordem')
    print(f'  5. Após treino, baixe o best.pt do Drive')
    print(f'  6. Copie para project_titan/models/')
    print(f'  7. Re-execute: python training/validate_pipeline.py')
    print()
    print(f'  📋 DIAGNÓSTICO DO MODELO ATUAL:')
    print(f'     mAP50: 0.874 (alvo: ≥ 0.95)')
    print(f'     Precision: 0.936')
    print(f'     Recall: 0.766 (alvo: ≥ 0.90)')
    print(f'     fold: 0.394 ← CRÍTICO')
    print(f'     check: 0.488 ← CRÍTICO')
    print(f'     raise: 0.548 ← CRÍTICO')
    print(f'     Cards: 0.84-0.90 (precisa subir para ≥0.95)')


if __name__ == '__main__':
    main()

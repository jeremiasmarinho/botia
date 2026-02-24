import shutil
import os
from pathlib import Path

# Caminhos
ANNOTATED_IMAGES = Path('data/to_annotate/images')
ANNOTATED_LABELS = Path('data/to_annotate/labels')
TARGET_IMAGES = Path('datasets/titan_cards/images')
TARGET_LABELS = Path('datasets/titan_cards/labels')

# Cria pastas destino se não existirem
TARGET_IMAGES.mkdir(parents=True, exist_ok=True)
TARGET_LABELS.mkdir(parents=True, exist_ok=True)

def merge_folder(src, dst):
    for file in src.glob('*'):
        if file.is_file():
            shutil.copy2(file, dst / file.name)

if ANNOTATED_IMAGES.exists() and ANNOTATED_LABELS.exists():
    merge_folder(ANNOTATED_IMAGES, TARGET_IMAGES)
    merge_folder(ANNOTATED_LABELS, TARGET_LABELS)
    print('Dados anotados mesclados com sucesso!')
else:
    print('Pastas de imagens/labels anotadas não encontradas.')

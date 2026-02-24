import os
import shutil
from pathlib import Path

# Detecta estrutura de exportação YOLO (CVAT, Roboflow, Label Studio)
EXPORT_ROOT = Path('data/to_annotate')
IMAGES_DIR = EXPORT_ROOT / 'images'
LABELS_DIR = EXPORT_ROOT / 'labels'

# Cria pastas destino
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
LABELS_DIR.mkdir(parents=True, exist_ok=True)

# Move imagens e labels para as pastas corretas
for file in EXPORT_ROOT.iterdir():
    if file.is_file():
        if file.suffix.lower() in ['.jpg', '.jpeg', '.png', '.bmp']:
            shutil.move(str(file), str(IMAGES_DIR / file.name))
        elif file.suffix.lower() == '.txt':
            shutil.move(str(file), str(LABELS_DIR / file.name))

print('Exportação YOLO organizada em data/to_annotate/images e data/to_annotate/labels.')

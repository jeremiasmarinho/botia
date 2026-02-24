import os
import zipfile

# Caminho da pasta do dataset
DATASET_DIR = os.path.join(os.path.dirname(__file__), '..', 'datasets', 'titan_cards')
# Caminho do arquivo zip de saída
ZIP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'titan_dataset.zip'))

def zip_dataset(dataset_dir, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(dataset_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, dataset_dir)
                zipf.write(file_path, arcname)
    print(f"Dataset compactado em: {zip_path}")

if __name__ == '__main__':
    if not os.path.exists(DATASET_DIR):
        print(f"Pasta do dataset não encontrada: {DATASET_DIR}")
    else:
        zip_dataset(DATASET_DIR, ZIP_PATH)

# Como treinar o modelo YOLOv8s no Google Colab

## 1. Compacte o dataset

No seu PC, compacte as pastas:
- `datasets/synthetic_v3/`
- `datasets/titan_cards/`
- `datasets/synthetic/` (opcional)
- `training/data.yaml`

Sugestão: crie um arquivo `titan_dataset.zip` com essas pastas/arquivos.

## 2. Faça upload para o Google Drive
- Crie uma pasta no seu Drive chamada `titan_colab`.
- Faça upload do `titan_dataset.zip` para essa pasta.

## 3. Abra o notebook Colab
- Use o notebook já existente: `training/colab_hybrid_train.ipynb`.
- Faça upload desse notebook para o Colab ou abra direto do Drive.

## 4. No Colab, execute as células:
1. **Montar o Google Drive**
2. **Instalar dependências** (ultralytics, etc)
3. **Descompactar o dataset**
4. **Rodar o treino**:
   ```python
   !yolo task=detect mode=train model=yolov8s.pt data=training/data.yaml epochs=150 batch=16 imgsz=640
   ```

## 5. Download do modelo treinado
- O modelo final estará em `/content/runs/detect/exp/weights/best.pt`.
- Faça download para seu PC e substitua em `models/titan_v8.pt`.

---

Se quiser, posso gerar um notebook Colab pronto com todos os comandos e instruções em português.
@echo off
REM Treina o modelo YOLOv8s com os dados atualizados
cd /d %~dp0..
python training/train_yolo.py --data training/data.yaml --model yolov8s.pt --epochs 150 --batch 16
pause
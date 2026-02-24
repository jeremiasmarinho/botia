@echo off
REM Avalia o modelo atual titan_v7_hybrid.pt
cd /d %~dp0..
python training/evaluate_yolo.py --model models/titan_v7_hybrid.pt --data training/data.yaml
pause
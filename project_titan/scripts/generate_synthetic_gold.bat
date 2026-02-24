@echo off
REM Script para gerar 5000 imagens sintéticas PPPoker com gold border
cd /d %~dp0..
python training/generate_pppoker_data.py --gold-border --num-images 5000 --output datasets/synthetic_v4
pause
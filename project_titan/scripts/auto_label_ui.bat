@echo off
REM Auto-labeler para botões, pot e stack
cd /d %~dp0..
python tools/auto_labeler.py --input data/to_annotate --output datasets/titan_cards_v2
pause
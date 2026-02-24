@echo off
REM Anotação manual de board cards
cd /d %~dp0..
python tools/card_annotator.py --input datasets/titan_cards_v2/images --board-only
pause
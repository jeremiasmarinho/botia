@echo off
REM Script para capturar 1000 frames reais do PPPoker
cd /d %~dp0..
python training/capture_frames.py --fps 0.5 --max 1000 --output data/to_annotate --showdown-only
pause
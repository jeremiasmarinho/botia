@echo off
REM Instala PaddleOCR e dependências
cd /d %~dp0..
.\.venv\Scripts\activate
pip install paddlepaddle paddleocr
pause
@echo off
REM ═══════════════════════════════════════════════════════════════════
REM   Titan Edge AI — Shadow Mode (Hero Reconnaissance)
REM   Standalone entry point: boots GameLoop in observation-only mode.
REM   YOLO inference runs, Stability Gate logs readings, NO ADB taps.
REM
REM   Prerequisites:
REM     1. LDPlayer running with a PPPoker table open
REM     2. USB debugging enabled (adb devices shows emulator-5554)
REM     3. npm install completed in titan-edge/
REM
REM   Output:
REM     [SHADOW MODE] Mesa Estável! Hero: [Ah, Kd, ...] | Board: [Ts, 9h, 2d] | Confiança: 96% | Latência YOLO: 18ms
REM     Only fires on stable readings (3+ identical frames). No spam.
REM ═══════════════════════════════════════════════════════════════════

cd /d "%~dp0"
echo.
echo   ╔═══════════════════════════════════════════╗
echo   ║   TITAN EDGE AI — SHADOW MODE STARTING   ║
echo   ║   Press Ctrl+C to exit                    ║
echo   ╚═══════════════════════════════════════════╝
echo.

npx electron src/main/shadow-mode.js

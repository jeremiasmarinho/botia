@echo off
setlocal

echo === Project Titan: Hive + Squad ===
echo.

REM Resolve venv Python. Prefer .venv in project root, then workspace root.
set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else if exist "%~dp0..\.venv\Scripts\python.exe" (
    set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
)

echo Python: %PYTHON%
echo.

cd /d "%~dp0"

echo [1/4] Starting HiveBrain (ZMQ server)...
start "HiveBrain" cmd /k "%PYTHON% -m core.hive_brain"
timeout /t 2 /nobreak >nul

echo [2/4] Starting Agent 01 (table_alpha)...
start "Agent 01" cmd /k "set TITAN_AGENT_ID=01&& set TITAN_TABLE_ID=table_alpha&& %PYTHON% -m agent.poker_agent"

echo [3/4] Starting Agent 02 (table_alpha)...
start "Agent 02" cmd /k "set TITAN_AGENT_ID=02&& set TITAN_TABLE_ID=table_alpha&& %PYTHON% -m agent.poker_agent"

echo [4/4] Starting Orchestrator...
start "Orchestrator" cmd /k "%PYTHON% -m orchestrator.engine"

echo.
echo All processes launched. Close individual windows to stop.

endlocal

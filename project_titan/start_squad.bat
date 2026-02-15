@echo off
setlocal

echo Starting Project Titan (Compozy-like)...
start "Orchestrator" cmd /k "python -m orchestrator.engine"

endlocal

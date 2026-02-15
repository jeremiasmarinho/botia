@echo off
setlocal

echo Starting Project Titan (Hive + Squad + Orchestrator)...
start "HiveBrain" cmd /k "python -m core.hive_brain"
start "Agent 01" cmd /k "set TITAN_AGENT_ID=01 && set TITAN_TABLE_ID=table_alpha && python -m agent.poker_agent"
start "Agent 02" cmd /k "set TITAN_AGENT_ID=02 && set TITAN_TABLE_ID=table_alpha && python -m agent.poker_agent"
start "Orchestrator" cmd /k "python -m orchestrator.engine"

endlocal

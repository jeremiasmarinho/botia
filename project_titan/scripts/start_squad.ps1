<#
.SYNOPSIS
  Launch the full Project Titan squad: HiveBrain + N agents + Orchestrator.
.DESCRIPTION
  Starts each component in its own background job, waits for user
  Ctrl-C, then terminates all children.
.PARAMETER Agents
  Number of agents to launch on the same table (default: 2).
.PARAMETER TableId
  Table ID for all agents (default: table_alpha).
.PARAMETER MaxCycles
  Limit each agent to N cycles (useful for testing).  0 = unlimited.
.PARAMETER SimScenario
  Simulation scenario forwarded to agents (e.g. cycle, fold, call).
.EXAMPLE
  .\scripts\start_squad.ps1
.EXAMPLE
  .\scripts\start_squad.ps1 -Agents 3 -TableId table_beta -MaxCycles 10
#>

[CmdletBinding()]
param(
    [int]$Agents = 2,
    [string]$TableId = "table_alpha",
    [int]$MaxCycles = 0,
    [string]$SimScenario = "",
    [switch]$UseMockVision
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# -------------------------------------------------------------------
# Resolve Python executable
# -------------------------------------------------------------------
function Find-Python {
    $candidates = @(
        (Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"),
        (Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe")
    )
    foreach ($p in $candidates) {
        $resolved = Resolve-Path $p -ErrorAction SilentlyContinue
        if ($resolved -and (Test-Path $resolved.Path)) { return $resolved.Path }
    }
    return "python"
}

$Python = Find-Python
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "=== Project Titan: Squad Launcher ===" -ForegroundColor Cyan
Write-Host "Python   : $Python"
Write-Host "Agents   : $Agents"
Write-Host "Table    : $TableId"
Write-Host "MaxCycles: $(if ($MaxCycles -gt 0) { $MaxCycles } else { 'unlimited' })"
Write-Host "MockVision: $(if ($UseMockVision) { 'ON' } else { 'OFF' })"
Write-Host ""

# -------------------------------------------------------------------
# Build env block shared by all agents
# -------------------------------------------------------------------
$agentEnv = @{}
$agentEnv["TITAN_TABLE_ID"] = $TableId
if ($MaxCycles -gt 0) { $agentEnv["TITAN_AGENT_MAX_CYCLES"] = "$MaxCycles" }
if ($SimScenario) { $agentEnv["TITAN_SIM_SCENARIO"] = $SimScenario }

if ($UseMockVision) {
    $env:TITAN_USE_MOCK_VISION = "1"
    $agentEnv["TITAN_USE_MOCK_VISION"] = "1"
    Write-Host "MODO DE TREINO: VISÃO SIMULADA" -ForegroundColor Yellow
}
else {
    $env:TITAN_USE_MOCK_VISION = "0"
    if ($agentEnv.ContainsKey("TITAN_USE_MOCK_VISION")) {
        $agentEnv.Remove("TITAN_USE_MOCK_VISION")
    }
}

# -------------------------------------------------------------------
# Launch processes
# -------------------------------------------------------------------
$procs = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Start-Component {
    param([string]$Title, [hashtable]$EnvVars, [string[]]$PythonArgs)

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $Python
    $psi.Arguments = ($PythonArgs -join " ")
    $psi.WorkingDirectory = $ProjectDir
    $psi.UseShellExecute = $false

    foreach ($kv in $EnvVars.GetEnumerator()) {
        $psi.EnvironmentVariables[$kv.Key] = $kv.Value
    }

    $proc = [System.Diagnostics.Process]::Start($psi)
    Write-Host "[+] $Title  PID=$($proc.Id)" -ForegroundColor Green
    return $proc
}

# 1) HiveBrain
$procs.Add((Start-Component -Title "HiveBrain" -EnvVars @{} -PythonArgs @("-m", "core.hive_brain")))
Start-Sleep -Seconds 1

# 2) Agents
for ($i = 1; $i -le $Agents; $i++) {
    $id = "{0:D2}" -f $i
    $env = @{} + $agentEnv
    $env["TITAN_AGENT_ID"] = $id
    $procs.Add((Start-Component -Title "Agent $id" -EnvVars $env -PythonArgs @("-m", "agent.poker_agent")))
}

# 3) Orchestrator
$procs.Add((Start-Component -Title "Orchestrator" -EnvVars @{} -PythonArgs @("-m", "orchestrator.engine")))

Write-Host ""
Write-Host "All components launched. Press Ctrl+C to stop." -ForegroundColor Yellow

# -------------------------------------------------------------------
# Wait / cleanup
# -------------------------------------------------------------------
try {
    while ($true) {
        $alive = $procs | Where-Object { -not $_.HasExited }
        if ($alive.Count -eq 0) {
            Write-Host "All processes exited." -ForegroundColor Cyan
            break
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    foreach ($p in $procs) {
        if (-not $p.HasExited) {
            try { $p.Kill() } catch { }
            Write-Host "[x] Stopped PID=$($p.Id)" -ForegroundColor Red
        }
    }
}

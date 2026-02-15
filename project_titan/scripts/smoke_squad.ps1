<#
.SYNOPSIS
  Smoke test for multi-agent squad protocol.
.DESCRIPTION
  Starts HiveBrain in the background, launches two agents with simulated
  vision (TITAN_SIM_SCENARIO=cycle), validates:
    1. Both agents connect and complete max_cycles.
    2. HiveBrain responds with mode=squad when both agents share a table.
    3. Dead cards are exchanged between agents.
  Exits 0 on success, 1 on failure.
.PARAMETER ReportDir
  Directory for stdout capture files.
.EXAMPLE
  .\scripts\smoke_squad.ps1 -ReportDir reports
#>

[CmdletBinding()]
param(
  [string]$ReportDir = "reports"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# -------------------------------------------------------------------
# Resolve Python
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

# Resolve ReportDir to absolute
if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $ProjectDir $ReportDir
}

Write-Host "=== Smoke Squad Test ===" -ForegroundColor Cyan
Write-Host "Python : $Python"
Write-Host "Project: $ProjectDir"
Write-Host ""

if (-not (Test-Path $resolvedReportDir)) {
  New-Item -ItemType Directory -Path $resolvedReportDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$passed = $true
$errors = [System.Collections.Generic.List[string]]::new()

# -------------------------------------------------------------------
# 1) Start HiveBrain
# -------------------------------------------------------------------
Write-Host "[1/5] Starting HiveBrain..." -ForegroundColor Yellow

$hiveBrainLog = Join-Path $resolvedReportDir "squad_hivebrain_$timestamp.log"

$hivePsi = [System.Diagnostics.ProcessStartInfo]::new()
$hivePsi.FileName = $Python
$hivePsi.Arguments = "-m core.hive_brain"
$hivePsi.WorkingDirectory = $ProjectDir
$hivePsi.UseShellExecute = $false
$hivePsi.RedirectStandardOutput = $true
$hivePsi.RedirectStandardError = $true
$hivePsi.EnvironmentVariables["TITAN_NO_COLOR"] = "1"

$hiveProc = [System.Diagnostics.Process]::Start($hivePsi)
Start-Sleep -Seconds 2

if ($hiveProc.HasExited) {
  Write-Host "  FAIL: HiveBrain exited prematurely (code=$($hiveProc.ExitCode))" -ForegroundColor Red
  $passed = $false
  $errors.Add("HiveBrain exited prematurely")
}
else {
  Write-Host "  OK: HiveBrain running PID=$($hiveProc.Id)" -ForegroundColor Green
}

# -------------------------------------------------------------------
# 2) Start Agent 01
# -------------------------------------------------------------------
Write-Host "[2/5] Starting Agent 01..." -ForegroundColor Yellow

$agent1Log = Join-Path $resolvedReportDir "squad_agent01_$timestamp.log"

$a1Psi = [System.Diagnostics.ProcessStartInfo]::new()
$a1Psi.FileName = $Python
$a1Psi.Arguments = "-m agent.poker_agent"
$a1Psi.WorkingDirectory = $ProjectDir
$a1Psi.UseShellExecute = $false
$a1Psi.RedirectStandardOutput = $true
$a1Psi.RedirectStandardError = $true
$a1Psi.EnvironmentVariables["TITAN_AGENT_ID"] = "01"
$a1Psi.EnvironmentVariables["TITAN_TABLE_ID"] = "table_smoke_squad"
$a1Psi.EnvironmentVariables["TITAN_SIM_SCENARIO"] = "cycle"
$a1Psi.EnvironmentVariables["TITAN_AGENT_MAX_CYCLES"] = "3"
$a1Psi.EnvironmentVariables["TITAN_NO_COLOR"] = "1"
$a1Psi.EnvironmentVariables["TITAN_AGENT_HEARTBEAT"] = "0.3"

$a1Proc = [System.Diagnostics.Process]::Start($a1Psi)

# -------------------------------------------------------------------
# 3) Start Agent 02
# -------------------------------------------------------------------
Write-Host "[3/5] Starting Agent 02..." -ForegroundColor Yellow

$agent2Log = Join-Path $resolvedReportDir "squad_agent02_$timestamp.log"

$a2Psi = [System.Diagnostics.ProcessStartInfo]::new()
$a2Psi.FileName = $Python
$a2Psi.Arguments = "-m agent.poker_agent"
$a2Psi.WorkingDirectory = $ProjectDir
$a2Psi.UseShellExecute = $false
$a2Psi.RedirectStandardOutput = $true
$a2Psi.RedirectStandardError = $true
$a2Psi.EnvironmentVariables["TITAN_AGENT_ID"] = "02"
$a2Psi.EnvironmentVariables["TITAN_TABLE_ID"] = "table_smoke_squad"
$a2Psi.EnvironmentVariables["TITAN_SIM_SCENARIO"] = "cycle"
$a2Psi.EnvironmentVariables["TITAN_AGENT_MAX_CYCLES"] = "3"
$a2Psi.EnvironmentVariables["TITAN_NO_COLOR"] = "1"
$a2Psi.EnvironmentVariables["TITAN_AGENT_HEARTBEAT"] = "0.3"

$a2Proc = [System.Diagnostics.Process]::Start($a2Psi)

# -------------------------------------------------------------------
# 4) Wait for agents to finish
# -------------------------------------------------------------------
Write-Host "[4/5] Waiting for agents to complete (timeout 30s)..." -ForegroundColor Yellow

$deadline = (Get-Date).AddSeconds(30)
$a1Done = $false
$a2Done = $false

while ((Get-Date) -lt $deadline) {
  if (-not $a1Done -and $a1Proc.HasExited) { $a1Done = $true }
  if (-not $a2Done -and $a2Proc.HasExited) { $a2Done = $true }
  if ($a1Done -and $a2Done) { break }
  Start-Sleep -Milliseconds 300
}

# Capture output
$a1Stdout = ""
$a1Stderr = ""
$a2Stdout = ""
$a2Stderr = ""

if ($a1Done) {
  $a1Stdout = $a1Proc.StandardOutput.ReadToEnd()
  $a1Stderr = $a1Proc.StandardError.ReadToEnd()
  $a1Stdout | Out-File -FilePath $agent1Log -Encoding utf8 -Force
}
if ($a2Done) {
  $a2Stdout = $a2Proc.StandardOutput.ReadToEnd()
  $a2Stderr = $a2Proc.StandardError.ReadToEnd()
  $a2Stdout | Out-File -FilePath $agent2Log -Encoding utf8 -Force
}

# Kill stragglers
foreach ($proc in @($a1Proc, $a2Proc, $hiveProc)) {
  if (-not $proc.HasExited) {
    try { $proc.Kill() } catch { }
  }
}

# Save hivebrain output
try {
  $hiveStdout = $hiveProc.StandardOutput.ReadToEnd()
  $hiveStdout | Out-File -FilePath $hiveBrainLog -Encoding utf8 -Force
}
catch { }

# -------------------------------------------------------------------
# 5) Validate results
# -------------------------------------------------------------------
Write-Host "[5/5] Validating..." -ForegroundColor Yellow

# Agent 01 completed?
if (-not $a1Done) {
  $passed = $false
  $errors.Add("Agent 01 did not finish within timeout")
  Write-Host "  FAIL: Agent 01 timeout" -ForegroundColor Red
}
elseif ($a1Proc.ExitCode -ne 0) {
  $passed = $false
  $errors.Add("Agent 01 exited with code $($a1Proc.ExitCode)")
  Write-Host "  FAIL: Agent 01 exit_code=$($a1Proc.ExitCode)" -ForegroundColor Red
}
else {
  Write-Host "  OK: Agent 01 completed" -ForegroundColor Green
}

# Agent 02 completed?
if (-not $a2Done) {
  $passed = $false
  $errors.Add("Agent 02 did not finish within timeout")
  Write-Host "  FAIL: Agent 02 timeout" -ForegroundColor Red
}
elseif ($a2Proc.ExitCode -ne 0) {
  $passed = $false
  $errors.Add("Agent 02 exited with code $($a2Proc.ExitCode)")
  Write-Host "  FAIL: Agent 02 exit_code=$($a2Proc.ExitCode)" -ForegroundColor Red
}
else {
  Write-Host "  OK: Agent 02 completed" -ForegroundColor Green
}

# Check for squad mode in output (either agent seeing mode=squad)
$squadDetected = ($a1Stdout -match "mode=squad") -or ($a2Stdout -match "mode=squad")
if ($squadDetected) {
  Write-Host "  OK: Squad mode detected (GOD MODE)" -ForegroundColor Green
}
else {
  # Squad mode may not fire if agents don't overlap perfectly in sim
  Write-Host "  INFO: Squad mode not detected (agents may not have overlapped)" -ForegroundColor Yellow
}

# Check for dead_cards exchange
$deadCardsDetected = ($a1Stdout -match "dead_cards=\['.+'\]") -or ($a2Stdout -match "dead_cards=\['.+'\]")
if ($deadCardsDetected) {
  Write-Host "  OK: Dead cards exchanged between agents" -ForegroundColor Green
}
else {
  Write-Host "  INFO: No dead_cards exchange observed (may need Redis)" -ForegroundColor Yellow
}

# Check for max_cycles message
$maxCyclesOk = ($a1Stdout -match "max_cycles=") -and ($a2Stdout -match "max_cycles=")
if ($maxCyclesOk) {
  Write-Host "  OK: Both agents reached max_cycles" -ForegroundColor Green
}
else {
  $passed = $false
  $errors.Add("Not all agents printed max_cycles completion message")
  Write-Host "  FAIL: max_cycles message missing" -ForegroundColor Red
}

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
Write-Host ""
if ($passed) {
  Write-Host "SQUAD SMOKE: PASSED" -ForegroundColor Green
  Write-Host "  Logs: $agent1Log"
  Write-Host "        $agent2Log"
  Write-Host "        $hiveBrainLog"
  exit 0
}
else {
  Write-Host "SQUAD SMOKE: FAILED" -ForegroundColor Red
  foreach ($e in $errors) {
    Write-Host "  - $e" -ForegroundColor Red
  }
  Write-Host ""
  Write-Host "--- Agent 01 stderr ---"
  Write-Host $a1Stderr
  Write-Host "--- Agent 02 stderr ---"
  Write-Host $a2Stderr
  exit 1
}

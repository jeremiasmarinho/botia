param(
  [string]$ReportDir = "reports"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $projectRoot $ReportDir
}

function Get-LatestSweepFile {
  param(
    [string]$Directory,
    [string]$Mode
  )

  return Get-ChildItem -Path $Directory -Filter "sweep_summary_${Mode}_*.json" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
}

function Invoke-CheckedCommand {
  param(
    [scriptblock]$Command,
    [string]$Label
  )

  Write-Host "[SMOKE] $Label"
  $output = & $Command 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) {
    throw "Falha em '$Label' (exit=$LASTEXITCODE)`n$output"
  }
  return $output
}

New-Item -ItemType Directory -Path $resolvedReportDir -Force | Out-Null

$beforeProfile = Get-LatestSweepFile -Directory $resolvedReportDir -Mode "profile"
$beforePosition = Get-LatestSweepFile -Directory $resolvedReportDir -Mode "position"
$startedAt = Get-Date

Invoke-CheckedCommand -Label "ProfileSweep (simulado curto)" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1 -SimScenario cycle -Ticks 6 -TickSeconds 0.1 -Opponents 2 -Simulations 1200 -DynamicSimulations -ProfileSweep -ReportDir $resolvedReportDir
} | Out-Null

Invoke-CheckedCommand -Label "PositionSweep (simulado curto)" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1 -SimScenario cycle -Ticks 6 -TickSeconds 0.1 -Opponents 2 -Simulations 1200 -DynamicSimulations -PositionSweep -ReportDir $resolvedReportDir
} | Out-Null

$afterProfile = Get-LatestSweepFile -Directory $resolvedReportDir -Mode "profile"
$afterPosition = Get-LatestSweepFile -Directory $resolvedReportDir -Mode "position"

if ($null -eq $afterProfile) {
  throw "Não foi gerado sweep summary de profile em '$resolvedReportDir'."
}
if ($null -eq $afterPosition) {
  throw "Não foi gerado sweep summary de position em '$resolvedReportDir'."
}

$hasNewProfile = ($null -eq $beforeProfile) -or ($afterProfile.FullName -ne $beforeProfile.FullName) -or ($afterProfile.LastWriteTime -ge $startedAt)
$hasNewPosition = ($null -eq $beforePosition) -or ($afterPosition.FullName -ne $beforePosition.FullName) -or ($afterPosition.LastWriteTime -ge $startedAt)

if (-not $hasNewProfile) {
  throw "Sweep profile não atualizou arquivo summary (latest=$($afterProfile.FullName))."
}
if (-not $hasNewPosition) {
  throw "Sweep position não atualizou arquivo summary (latest=$($afterPosition.FullName))."
}

Write-Host "[SMOKE] OK profile_summary=$($afterProfile.Name)"
Write-Host "[SMOKE] OK position_summary=$($afterPosition.Name)"

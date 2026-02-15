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

$outputMode = Invoke-CheckedCommand -Label "run_windows with LabelMode" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1 -PrintBaselineJson -UseBestBaseline -LabelMode dataset_v1 -ReportDir $resolvedReportDir
}

if ($outputMode -notmatch "TITAN_VISION_LABEL_PROFILE=dataset_v1") {
  throw "Saída não confirmou LabelMode=dataset_v1"
}

$outputAlias = Invoke-CheckedCommand -Label "run_windows with legacy LabelProfile alias" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1 -PrintBaselineJson -UseBestBaseline -LabelProfile dataset_v1 -ReportDir $resolvedReportDir
}

if ($outputAlias -notmatch "TITAN_VISION_LABEL_PROFILE=dataset_v1") {
  throw "Saída não confirmou alias LabelProfile=dataset_v1"
}

$outputJson = Invoke-CheckedCommand -Label "print_baseline JSON" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\print_baseline.ps1 -ReportDir $resolvedReportDir -Json
}

$jsonMatch = [regex]::Match($outputJson, "\{[\s\S]*\}")
if (-not $jsonMatch.Success) {
  throw "Não foi possível localizar payload JSON em print_baseline"
}

$payload = $jsonMatch.Value | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$payload.table_profile) -or [string]::IsNullOrWhiteSpace([string]$payload.table_position)) {
  throw "Payload JSON de baseline incompleto"
}

Write-Host "[SMOKE] OK baseline profile=$($payload.table_profile) position=$($payload.table_position)"

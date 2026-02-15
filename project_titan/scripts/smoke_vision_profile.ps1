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

New-Item -ItemType Directory -Path $resolvedReportDir -Force | Out-Null

Invoke-CheckedCommand -Label "vision profile run #1" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\vision_profile.ps1 -Frames 10 -TargetFps 30 -NoSamples -ReportDir $resolvedReportDir
} | Out-Null

Invoke-CheckedCommand -Label "vision profile run #2" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\vision_profile.ps1 -Frames 10 -TargetFps 30 -NoSamples -ReportDir $resolvedReportDir
} | Out-Null

$outputCompare = Invoke-CheckedCommand -Label "vision profile compare" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\vision_profile_compare.ps1 -ReportDir $resolvedReportDir -Json -SaveCompare -SaveLatest
}

$jsonMatch = [regex]::Match($outputCompare, "\{[\s\S]*\}")
if (-not $jsonMatch.Success) {
  throw "Não foi possível localizar payload JSON em vision_profile_compare"
}

$payload = $jsonMatch.Value | ConvertFrom-Json
if ($null -eq $payload.latest -or $null -eq $payload.previous -or $null -eq $payload.deltas) {
  throw "Payload de comparação incompleto"
}

if ([string]::IsNullOrWhiteSpace([string]$payload.status)) {
  throw "Payload de comparação sem status"
}

if ($null -eq $payload.ci -or $null -eq $payload.ci.exit_code) {
  throw "Payload de comparação sem bloco ci"
}

if ([string]::IsNullOrWhiteSpace([string]$payload.latest_file) -or -not (Test-Path ([string]$payload.latest_file))) {
  throw "Arquivo latest de comparação não encontrado"
}

Write-Host "[SMOKE] OK vision_compare status=$([string]$payload.status) latest=$([System.IO.Path]::GetFileName([string]$payload.latest.file)) previous=$([System.IO.Path]::GetFileName([string]$payload.previous.file)) p95_delta_pct=$([math]::Round([double]$payload.deltas.p95_pct, 3))"

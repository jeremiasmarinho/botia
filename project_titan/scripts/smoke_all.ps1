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

  Write-Host "[SMOKE-ALL] $Label"
  $output = & $Command 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) {
    throw "Falha em '$Label' (exit=$LASTEXITCODE)`n$output"
  }
  return $output
}

$startedAt = Get-Date

Invoke-CheckedCommand -Label "Baseline smoke" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\smoke_baseline.ps1 -ReportDir $resolvedReportDir
} | Out-Null

Invoke-CheckedCommand -Label "Sweep smoke" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\smoke_sweep.ps1 -ReportDir $resolvedReportDir
} | Out-Null

Invoke-CheckedCommand -Label "Vision profile smoke" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\smoke_vision_profile.ps1 -ReportDir $resolvedReportDir
} | Out-Null

Invoke-CheckedCommand -Label "Squad smoke" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\smoke_squad.ps1 -ReportDir $resolvedReportDir
} | Out-Null

$duration = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 2)

Invoke-CheckedCommand -Label "Smoke health summary" -Command {
  powershell -ExecutionPolicy Bypass -File .\scripts\smoke_health_summary.ps1 -ReportDir $resolvedReportDir -DurationSeconds $duration
} | Out-Null

Write-Host "[SMOKE-ALL] OK duration_seconds=$duration report_dir=$resolvedReportDir"

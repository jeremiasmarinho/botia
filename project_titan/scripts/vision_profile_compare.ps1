param(
  [string]$ReportDir = "reports",
  [ValidateRange(0, 500)]
  [double]$P95RegressionThresholdPct = 15.0,
  [switch]$FailOnRegression,
  [switch]$SaveCompare,
  [switch]$SaveLatest,
  [string]$LatestFileName = "vision_profile_compare_latest.json",
  [switch]$Json
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $projectRoot $ReportDir
}

if (-not (Test-Path $resolvedReportDir)) {
  throw "Diretório de reports não encontrado: $resolvedReportDir"
}

$files = Get-ChildItem -Path $resolvedReportDir -Filter "vision_profile_*.json" -File -ErrorAction SilentlyContinue |
Where-Object { $_.Name -notlike "vision_profile_compare*" } |
Sort-Object LastWriteTime -Descending |
Select-Object -First 2

if ($null -eq $files -or $files.Count -lt 2) {
  throw "São necessários pelo menos 2 arquivos vision_profile_*.json em '$resolvedReportDir'."
}

$latestFile = $files[0]
$previousFile = $files[1]

$latest = Get-Content -Path $latestFile.FullName -Raw | ConvertFrom-Json
$previous = Get-Content -Path $previousFile.FullName -Raw | ConvertFrom-Json

if ($null -eq $latest.summary -or $null -eq $previous.summary) {
  throw "Payload inválido em um dos relatórios de visão (campo 'summary' ausente)."
}

$latestP95 = [double]$latest.summary.latency_ms_p95
$previousP95 = [double]$previous.summary.latency_ms_p95
$latestAvg = [double]$latest.summary.latency_ms_avg
$previousAvg = [double]$previous.summary.latency_ms_avg
$latestFps = [double]$latest.summary.achieved_fps
$previousFps = [double]$previous.summary.achieved_fps

$p95DeltaMs = $latestP95 - $previousP95
$p95DeltaPct = 0.0
if ([math]::Abs($previousP95) -gt 1e-9) {
  $p95DeltaPct = ($p95DeltaMs / $previousP95) * 100.0
}

$avgDeltaMs = $latestAvg - $previousAvg
$avgDeltaPct = 0.0
if ([math]::Abs($previousAvg) -gt 1e-9) {
  $avgDeltaPct = ($avgDeltaMs / $previousAvg) * 100.0
}

$fpsDelta = $latestFps - $previousFps

$isRegression = $p95DeltaPct -gt $P95RegressionThresholdPct
$status = if ($isRegression) { "fail" } else { "pass" }
$effectiveExitCode = if ($FailOnRegression -and $isRegression) { 1 } else { 0 }

$payload = [PSCustomObject]@{
  compared_at = (Get-Date).ToString("o")
  report_dir  = $resolvedReportDir
  status      = $status
  threshold   = [PSCustomObject]@{
    p95_regression_pct = [math]::Round($P95RegressionThresholdPct, 4)
  }
  latest      = [PSCustomObject]@{
    file           = $latestFile.FullName
    generated_at   = [string]$latest.generated_at
    frames         = [int]$latest.summary.frames
    achieved_fps   = [math]::Round($latestFps, 6)
    latency_ms_avg = [math]::Round($latestAvg, 6)
    latency_ms_p95 = [math]::Round($latestP95, 6)
  }
  previous    = [PSCustomObject]@{
    file           = $previousFile.FullName
    generated_at   = [string]$previous.generated_at
    frames         = [int]$previous.summary.frames
    achieved_fps   = [math]::Round($previousFps, 6)
    latency_ms_avg = [math]::Round($previousAvg, 6)
    latency_ms_p95 = [math]::Round($previousP95, 6)
  }
  deltas      = [PSCustomObject]@{
    p95_ms  = [math]::Round($p95DeltaMs, 6)
    p95_pct = [math]::Round($p95DeltaPct, 6)
    avg_ms  = [math]::Round($avgDeltaMs, 6)
    avg_pct = [math]::Round($avgDeltaPct, 6)
    fps     = [math]::Round($fpsDelta, 6)
  }
  regression  = [PSCustomObject]@{
    p95_regressed = [bool]$isRegression
  }
  ci          = [PSCustomObject]@{
    fail_on_regression = [bool]$FailOnRegression
    exit_code          = [int]$effectiveExitCode
    should_fail        = [bool]($effectiveExitCode -ne 0)
  }
}

if ($SaveCompare) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $target = Join-Path $resolvedReportDir "vision_profile_compare_$stamp.json"
  $payload | ConvertTo-Json -Depth 6 | Set-Content -Path $target -Encoding UTF8
  $payload | Add-Member -NotePropertyName compare_file -NotePropertyValue $target
}

if ($SaveLatest) {
  $safeLatestName = if ([string]::IsNullOrWhiteSpace($LatestFileName)) { "vision_profile_compare_latest.json" } else { $LatestFileName }
  $latestTarget = Join-Path $resolvedReportDir $safeLatestName
  $payload | ConvertTo-Json -Depth 6 | Set-Content -Path $latestTarget -Encoding UTF8
  if ($payload.PSObject.Properties["latest_file"]) {
    $payload.latest_file = $latestTarget
  }
  else {
    $payload | Add-Member -NotePropertyName latest_file -NotePropertyValue $latestTarget
  }
}

if ($Json) {
  $payload | ConvertTo-Json -Depth 6
}
else {
  Write-Host "[VISION-COMPARE] latest=$($latestFile.Name) previous=$($previousFile.Name)"
  Write-Host "[VISION-COMPARE] status=$status"
  Write-Host "[VISION-COMPARE] p95_ms latest=$([math]::Round($latestP95, 3)) previous=$([math]::Round($previousP95, 3)) delta_ms=$([math]::Round($p95DeltaMs, 3)) delta_pct=$([math]::Round($p95DeltaPct, 2))"
  Write-Host "[VISION-COMPARE] avg_ms latest=$([math]::Round($latestAvg, 3)) previous=$([math]::Round($previousAvg, 3)) delta_ms=$([math]::Round($avgDeltaMs, 3))"
  Write-Host "[VISION-COMPARE] achieved_fps latest=$([math]::Round($latestFps, 2)) previous=$([math]::Round($previousFps, 2)) delta=$([math]::Round($fpsDelta, 2))"

  if ($isRegression) {
    Write-Warning "Regressão detectada: p95 aumentou $([math]::Round($p95DeltaPct, 2))% (threshold=$P95RegressionThresholdPct%)."
  }
  else {
    Write-Host "[VISION-COMPARE] sem regressão de p95 (threshold=$P95RegressionThresholdPct%)."
  }

  if ($SaveCompare -and $payload.PSObject.Properties["compare_file"]) {
    Write-Host "[VISION-COMPARE] compare_file=$($payload.compare_file)"
  }
  if ($SaveLatest -and $payload.PSObject.Properties["latest_file"]) {
    Write-Host "[VISION-COMPARE] latest_file=$($payload.latest_file)"
  }
}

exit $effectiveExitCode

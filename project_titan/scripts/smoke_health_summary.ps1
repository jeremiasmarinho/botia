param(
  [string]$ReportDir = "reports",
  [double]$DurationSeconds = 0,
  [switch]$FailOnVisionRegression,
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

function Get-LatestFile {
  param(
    [string]$Directory,
    [string]$Filter
  )

  $file = Get-ChildItem -Path $Directory -Filter $Filter -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

  return $file
}

$latestRunReport = Get-LatestFile -Directory $resolvedReportDir -Filter "run_report_*.json"
$latestProfileSweep = Get-LatestFile -Directory $resolvedReportDir -Filter "sweep_summary_profile_*.json"
$latestPositionSweep = Get-LatestFile -Directory $resolvedReportDir -Filter "sweep_summary_position_*.json"
$latestVisionProfile = Get-ChildItem -Path $resolvedReportDir -Filter "vision_profile_*.json" -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -notlike "vision_profile_compare*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$latestVisionCompare = Get-LatestFile -Directory $resolvedReportDir -Filter "vision_profile_compare_latest.json"
$latestSquadLogA = Get-LatestFile -Directory $resolvedReportDir -Filter "squad_agent01_*.log"
$latestSquadLogB = Get-LatestFile -Directory $resolvedReportDir -Filter "squad_agent02_*.log"
$latestSquadLogHive = Get-LatestFile -Directory $resolvedReportDir -Filter "squad_hivebrain_*.log"

$visionComparePayload = $null
$visionCompareStatus = "unknown"
if ($null -ne $latestVisionCompare) {
  try {
    $visionComparePayload = Get-Content -Path $latestVisionCompare.FullName -Raw | ConvertFrom-Json
    if ($null -ne $visionComparePayload.status -and -not [string]::IsNullOrWhiteSpace([string]$visionComparePayload.status)) {
      $visionCompareStatus = [string]$visionComparePayload.status
    }
  }
  catch {
    $visionCompareStatus = "invalid"
  }
}

$checks = [PSCustomObject]@{
  baseline = [PSCustomObject]@{
    status = if ($null -ne $latestRunReport) { "pass" } else { "unknown" }
    artifact = if ($null -ne $latestRunReport) { $latestRunReport.FullName } else { $null }
  }
  sweep = [PSCustomObject]@{
    status = if ($null -ne $latestProfileSweep -and $null -ne $latestPositionSweep) { "pass" } else { "unknown" }
    profile_artifact = if ($null -ne $latestProfileSweep) { $latestProfileSweep.FullName } else { $null }
    position_artifact = if ($null -ne $latestPositionSweep) { $latestPositionSweep.FullName } else { $null }
  }
  vision_profile = [PSCustomObject]@{
    status = if ($null -ne $latestVisionProfile) { "pass" } else { "unknown" }
    profile_artifact = if ($null -ne $latestVisionProfile) { $latestVisionProfile.FullName } else { $null }
    compare_status = $visionCompareStatus
    compare_artifact = if ($null -ne $latestVisionCompare) { $latestVisionCompare.FullName } else { $null }
  }
  squad = [PSCustomObject]@{
    status = if ($null -ne $latestSquadLogA -and $null -ne $latestSquadLogB -and $null -ne $latestSquadLogHive) { "pass" } else { "unknown" }
    agent01_log = if ($null -ne $latestSquadLogA) { $latestSquadLogA.FullName } else { $null }
    agent02_log = if ($null -ne $latestSquadLogB) { $latestSquadLogB.FullName } else { $null }
    hivebrain_log = if ($null -ne $latestSquadLogHive) { $latestSquadLogHive.FullName } else { $null }
  }
}

$overallStatus = "pass"
if (
  $checks.baseline.status -ne "pass" -or
  $checks.sweep.status -ne "pass" -or
  $checks.vision_profile.status -ne "pass" -or
  $checks.squad.status -ne "pass"
) {
  $overallStatus = "unknown"
}
if ($FailOnVisionRegression -and $visionCompareStatus -eq "fail") {
  $overallStatus = "fail"
}

$payload = [PSCustomObject]@{
  generated_at = (Get-Date).ToString("o")
  report_dir = $resolvedReportDir
  duration_seconds = [math]::Round([double]$DurationSeconds, 3)
  overall_status = $overallStatus
  checks = $checks
  policy = [PSCustomObject]@{
    fail_on_vision_regression = [bool]$FailOnVisionRegression
  }
}

$latestPath = Join-Path $resolvedReportDir "smoke_health_latest.json"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$historyPath = Join-Path $resolvedReportDir "smoke_health_$stamp.json"

$payload | ConvertTo-Json -Depth 7 | Set-Content -Path $latestPath -Encoding UTF8
$payload | ConvertTo-Json -Depth 7 | Set-Content -Path $historyPath -Encoding UTF8

if ($Json) {
  $payload | ConvertTo-Json -Depth 7
}
else {
  Write-Host "[SMOKE-HEALTH] overall_status=$overallStatus duration_seconds=$([math]::Round([double]$DurationSeconds, 2))"
  Write-Host "[SMOKE-HEALTH] baseline=$($checks.baseline.status) sweep=$($checks.sweep.status) vision=$($checks.vision_profile.status) squad=$($checks.squad.status)"
  Write-Host "[SMOKE-HEALTH] vision_compare_status=$visionCompareStatus"
  Write-Host "[SMOKE-HEALTH] latest_file=$latestPath"
  Write-Host "[SMOKE-HEALTH] history_file=$historyPath"
}

if ($overallStatus -eq "fail") {
  exit 1
}

exit 0

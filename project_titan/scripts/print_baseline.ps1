param(
  [string]$ReportDir = "reports",
  [switch]$Json
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "baseline_resolver.ps1")

if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $projectRoot $ReportDir
}
$baseline = Resolve-TitanBaseline -Directory $resolvedReportDir -FallbackProfile "normal" -FallbackPosition "mp" -Quiet

if ($baseline.source -eq "manual") {
  throw "Baseline não encontrado em '$resolvedReportDir'. Rode um ProfileSweep e PositionSweep (ou salve baseline_best.json)."
}

$payload = [PSCustomObject]@{
  table_profile   = $baseline.table_profile
  table_position  = $baseline.table_position
  profile_source  = $baseline.profile_source
  position_source = $baseline.position_source
  source          = $baseline.source
  report_dir      = $resolvedReportDir
}

if ($Json) {
  $payload | ConvertTo-Json -Depth 5
}
else {
  Write-Host "[BASELINE] profile=$($payload.table_profile) source=$($payload.profile_source)"
  Write-Host "[BASELINE] position=$($payload.table_position) source=$($payload.position_source)"
  Write-Host "[BASELINE] source=$($payload.source)"
  Write-Host "[BASELINE] report_dir=$($payload.report_dir)"
}

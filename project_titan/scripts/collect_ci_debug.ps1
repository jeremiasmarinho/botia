param(
  [string]$ReportDir = "reports",
  [string]$OutputDir = "reports",
  [switch]$IncludeReports = $true,
  [int]$MaxReportFiles = 30
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $projectRoot $ReportDir
}

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
  $resolvedOutputDir = $OutputDir
}
else {
  $resolvedOutputDir = Join-Path $projectRoot $OutputDir
}

New-Item -ItemType Directory -Path $resolvedOutputDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stagingDir = Join-Path ([System.IO.Path]::GetTempPath()) "titan_ci_debug_staging_${timestamp}_$PID"
if (Test-Path $stagingDir) {
  Remove-Item -Path $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

function Copy-IfExists {
  param(
    [string]$SourcePath,
    [string]$TargetDir
  )

  if (-not (Test-Path $SourcePath)) {
    return
  }

  New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
  Copy-Item -Path $SourcePath -Destination $TargetDir -Recurse -Force
}

function Copy-RecentReportFiles {
  param(
    [string]$SourceDir,
    [string]$TargetDir,
    [int]$Limit
  )

  if (-not (Test-Path $SourceDir)) {
    return
  }

  $patterns = @(
    "baseline_best.json",
    "run_report_*.json",
    "sweep_summary_*.json",
    "history_compare_*.json"
  )

  New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

  $items = foreach ($pattern in $patterns) {
    Get-ChildItem -Path $SourceDir -Filter $pattern -File -ErrorAction SilentlyContinue
  }

  $selected = $items |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First $Limit

  foreach ($file in $selected) {
    Copy-Item -Path $file.FullName -Destination $TargetDir -Force
  }
}

# Core debug files
Copy-IfExists -SourcePath (Join-Path $projectRoot "README.md") -TargetDir (Join-Path $stagingDir "project_titan")
Copy-IfExists -SourcePath (Join-Path $projectRoot "scripts\run_windows.ps1") -TargetDir (Join-Path $stagingDir "project_titan\scripts")
Copy-IfExists -SourcePath (Join-Path $projectRoot "scripts\smoke_baseline.ps1") -TargetDir (Join-Path $stagingDir "project_titan\scripts")
Copy-IfExists -SourcePath (Join-Path $projectRoot "scripts\smoke_sweep.ps1") -TargetDir (Join-Path $stagingDir "project_titan\scripts")
Copy-IfExists -SourcePath (Join-Path $projectRoot "scripts\smoke_all.ps1") -TargetDir (Join-Path $stagingDir "project_titan\scripts")
Copy-IfExists -SourcePath (Join-Path $projectRoot "scripts\print_baseline.ps1") -TargetDir (Join-Path $stagingDir "project_titan\scripts")
Copy-IfExists -SourcePath (Join-Path $projectRoot "scripts\baseline_resolver.ps1") -TargetDir (Join-Path $stagingDir "project_titan\scripts")

# Repo-level governance files
Copy-IfExists -SourcePath (Join-Path $projectRoot "..\.github\workflows\project_titan_smoke.yml") -TargetDir (Join-Path $stagingDir ".github\workflows")
Copy-IfExists -SourcePath (Join-Path $projectRoot "..\.github\PULL_REQUEST_TEMPLATE.md") -TargetDir (Join-Path $stagingDir ".github")
Copy-IfExists -SourcePath (Join-Path $projectRoot "..\.github\CODEOWNERS") -TargetDir (Join-Path $stagingDir ".github")
Copy-IfExists -SourcePath (Join-Path $projectRoot "..\CONTRIBUTING.md") -TargetDir $stagingDir

if ($IncludeReports) {
  Copy-RecentReportFiles -SourceDir $resolvedReportDir -TargetDir (Join-Path $stagingDir "project_titan\reports") -Limit $MaxReportFiles
}

$zipPath = Join-Path $resolvedOutputDir "ci_debug_bundle_$timestamp.zip"
if (Test-Path $zipPath) {
  Remove-Item -Path $zipPath -Force
}

Compress-Archive -Path (Join-Path $stagingDir "*") -DestinationPath $zipPath -CompressionLevel Optimal
Remove-Item -Path $stagingDir -Recurse -Force

Write-Host "[CI-DEBUG] bundle=$zipPath"

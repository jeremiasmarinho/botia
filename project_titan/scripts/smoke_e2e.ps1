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

New-Item -ItemType Directory -Path $resolvedReportDir -Force | Out-Null

function Find-Python {
  $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) { return $venvPython }
  $venvPythonUnix = Join-Path $projectRoot ".venv/bin/python"
  if (Test-Path $venvPythonUnix) { return $venvPythonUnix }
  return "python"
}

$python = Find-Python
Write-Host "[SMOKE-E2E] Python: $python"

$smokeScript = Join-Path $projectRoot "tools\smoke_e2e.py"
if (-not (Test-Path $smokeScript)) {
  throw "Script de smoke E2E nao encontrado: $smokeScript"
}

$reportFile = Join-Path $resolvedReportDir "smoke_e2e_latest.json"
$output = & $python $smokeScript --save-report $reportFile 2>&1 | Out-String
Write-Host $output

if ($LASTEXITCODE -ne 0) {
  throw "[SMOKE-E2E] FAIL (exit=$LASTEXITCODE)"
}

Write-Host "[SMOKE-E2E] OK"

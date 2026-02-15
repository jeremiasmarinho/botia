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
Write-Host "[SMOKE-TRAINING] Python: $python"

$smokeScript = Join-Path $projectRoot "training\smoke_training.py"
if (-not (Test-Path $smokeScript)) {
  throw "Script de smoke training nao encontrado: $smokeScript"
}

$output = & $python $smokeScript --save-report (Join-Path $resolvedReportDir "smoke_training_latest.json") 2>&1 | Out-String
$exitCode = $LASTEXITCODE

Write-Host $output

if ($exitCode -ne 0) {
  throw "smoke_training.py falhou (exit=$exitCode)"
}

Write-Host "[SMOKE-TRAINING] OK"
exit 0

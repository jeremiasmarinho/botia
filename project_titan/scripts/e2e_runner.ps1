param(
  [ValidateSet("sim", "real")]
  [string]$Mode = "sim",

  [int]$Cycles = 5,

  [string]$Scenario = "cycle",

  [string]$Model = "",

  [switch]$Visual,

  [string]$SaveFrames = "",

  [string]$ReportDir = "reports",

  [double]$TickSeconds = 0.2,

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

New-Item -ItemType Directory -Path $resolvedReportDir -Force | Out-Null

function Find-Python {
  $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) { return $venvPython }
  $venvPythonUnix = Join-Path $projectRoot ".venv/bin/python"
  if (Test-Path $venvPythonUnix) { return $venvPythonUnix }
  return "python"
}

$python = Find-Python
$script = Join-Path $projectRoot "tools\e2e_runner.py"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$reportFile = Join-Path $resolvedReportDir "e2e_report_${stamp}.json"

$pythonArgs = @(
  $script
  "--mode", $Mode
  "--cycles", $Cycles.ToString()
  "--scenario", $Scenario
  "--tick-seconds", $TickSeconds.ToString()
  "--save-report", $reportFile
)

if ($Model -ne "") {
  $pythonArgs += @("--model", $Model)
}

if ($Visual) {
  $pythonArgs += "--visual"
}

if ($SaveFrames -ne "") {
  $resolvedFrames = if ([System.IO.Path]::IsPathRooted($SaveFrames)) { $SaveFrames } else { Join-Path $projectRoot $SaveFrames }
  $pythonArgs += @("--save-frames", $resolvedFrames)
}

if ($Json) {
  $pythonArgs += "--json"
}

Write-Host "[E2E-RUNNER] mode=$Mode cycles=$Cycles scenario=$Scenario"
& $python @pythonArgs

if ($LASTEXITCODE -ne 0) {
  throw "[E2E-RUNNER] FAIL (exit=$LASTEXITCODE)"
}

# Copy to latest
$latestFile = Join-Path $resolvedReportDir "e2e_report_latest.json"
Copy-Item -Path $reportFile -Destination $latestFile -Force

Write-Host "[E2E-RUNNER] report=$reportFile"
Write-Host "[E2E-RUNNER] latest=$latestFile"

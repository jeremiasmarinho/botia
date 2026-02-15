param(
  [ValidateRange(1, 20000)]
  [int]$Frames = 180,
  [ValidateRange(1, 120)]
  [double]$TargetFps = 30,
  [ValidateRange(0, 2000)]
  [int]$WarmupFrames = 5,
  [string]$ReportDir = "reports",
  [switch]$Json,
  [switch]$NoSamples
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Find-Python {
  $candidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot "..\.venv\Scripts\python.exe")
  )

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  return "python"
}

$pythonExe = Find-Python

if ([System.IO.Path]::IsPathRooted($ReportDir)) {
  $resolvedReportDir = $ReportDir
}
else {
  $resolvedReportDir = Join-Path $projectRoot $ReportDir
}

New-Item -ItemType Directory -Path $resolvedReportDir -Force | Out-Null

$pythonArgs = @(
  (Join-Path $PSScriptRoot "vision_profile.py"),
  "--frames", "$Frames",
  "--target-fps", "$TargetFps",
  "--warmup-frames", "$WarmupFrames",
  "--report-dir", "$resolvedReportDir"
)

if ($Json) {
  $pythonArgs += "--json"
}
if ($NoSamples) {
  $pythonArgs += "--no-samples"
}

Write-Host "[VISION-PROFILE] python=$pythonExe"
Write-Host "[VISION-PROFILE] frames=$Frames target_fps=$TargetFps warmup=$WarmupFrames"

& $pythonExe @pythonArgs
if ($LASTEXITCODE -ne 0) {
  throw "vision_profile falhou (exit=$LASTEXITCODE)"
}

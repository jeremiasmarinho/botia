param(
  [switch]$HealthOnly,
  [ValidateSet("off", "wait", "fold", "call", "raise", "cycle")]
  [string]$SimScenario = "off",
  [ValidateRange(0, 100000)]
  [int]$Ticks = 0,
  [ValidateRange(0.05, 10.0)]
  [double]$TickSeconds = 0.2,
  [string]$ReportDir = "",
  [switch]$OpenLastReport,
  [switch]$PrintLastReport,
  [string]$LabelMapFile = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonCandidates = @(
  (Join-Path $projectRoot ".venv\Scripts\python.exe"),
  (Join-Path (Split-Path -Parent $projectRoot) ".venv\Scripts\python.exe")
)

$pythonExe = $null
foreach ($candidate in $pythonCandidates) {
  if (Test-Path $candidate) {
    $pythonExe = $candidate
    break
  }
}

if (-not $pythonExe) {
  $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
  if ($pythonCmd) {
    $pythonExe = $pythonCmd.Source
  }
}

if (-not $pythonExe) {
  throw "Python não encontrado. Crie/ative um ambiente virtual e instale as dependências."
}

Push-Location $projectRoot
$_previousSimScenario = $env:TITAN_SIM_SCENARIO
$_previousMaxTicks = $env:TITAN_MAX_TICKS
$_previousTickSeconds = $env:TITAN_TICK_SECONDS
$_previousReportDir = $env:TITAN_REPORT_DIR
$_previousLabelMapFile = $env:TITAN_VISION_LABEL_MAP_FILE
$resolvedReportDir = $null

try {
  if ($SimScenario -ne "off") {
    $env:TITAN_SIM_SCENARIO = $SimScenario
    Write-Host "[SIM] TITAN_SIM_SCENARIO=$SimScenario"
  }

  if ($Ticks -gt 0) {
    $env:TITAN_MAX_TICKS = "$Ticks"
    Write-Host "[RUN] TITAN_MAX_TICKS=$Ticks"
  }

  $env:TITAN_TICK_SECONDS = "$TickSeconds"
  Write-Host "[RUN] TITAN_TICK_SECONDS=$TickSeconds"

  if (-not [string]::IsNullOrWhiteSpace($LabelMapFile)) {
    if ([System.IO.Path]::IsPathRooted($LabelMapFile)) {
      $resolvedLabelMapFile = $LabelMapFile
    }
    else {
      $resolvedLabelMapFile = Join-Path $projectRoot $LabelMapFile
    }
    $env:TITAN_VISION_LABEL_MAP_FILE = "$resolvedLabelMapFile"
    Write-Host "[RUN] TITAN_VISION_LABEL_MAP_FILE=$resolvedLabelMapFile"
  }

  if (-not [string]::IsNullOrWhiteSpace($ReportDir)) {
    if ([System.IO.Path]::IsPathRooted($ReportDir)) {
      $resolvedReportDir = $ReportDir
    }
    else {
      $resolvedReportDir = Join-Path $projectRoot $ReportDir
    }
  }
  elseif (($OpenLastReport -or $PrintLastReport) -and -not $HealthOnly) {
    $resolvedReportDir = Join-Path $projectRoot "reports"
  }

  if ($null -ne $resolvedReportDir) {
    $env:TITAN_REPORT_DIR = "$resolvedReportDir"
    Write-Host "[RUN] TITAN_REPORT_DIR=$resolvedReportDir"
  }

  Write-Host "[1/2] Running healthcheck with: $pythonExe"
  & $pythonExe -m orchestrator.healthcheck

  if ($LASTEXITCODE -ne 0) {
    throw "Healthcheck falhou com código $LASTEXITCODE"
  }

  if ($HealthOnly) {
    Write-Host "Healthcheck OK. Encerrando por -HealthOnly."
    exit 0
  }

  Write-Host "[2/2] Starting orchestrator engine... (Ctrl+C para parar)"
  & $pythonExe -m orchestrator.engine

  if ($LASTEXITCODE -ne 0) {
    throw "Engine finalizou com código $LASTEXITCODE"
  }

  if (($OpenLastReport -or $PrintLastReport) -and $null -ne $resolvedReportDir) {
    $latestReport = Get-ChildItem -Path $resolvedReportDir -Filter run_report_*.json -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

    if ($null -ne $latestReport) {
      if ($PrintLastReport) {
        Write-Host "[RUN] Latest report file: $($latestReport.FullName)"
        Write-Host "[RUN] Latest report JSON:"
        Get-Content -Path $latestReport.FullName -Raw
      }

      if ($OpenLastReport) {
        try {
          Start-Process -FilePath $latestReport.FullName | Out-Null
          Write-Host "[RUN] Opened latest report: $($latestReport.FullName)"
        }
        catch {
          Write-Warning "Não foi possível abrir o relatório automaticamente: $($latestReport.FullName)"
        }
      }
    }
    else {
      Write-Warning "Nenhum relatório encontrado em: $resolvedReportDir"
    }
  }
}
finally {
  if ($null -eq $_previousSimScenario) {
    Remove-Item Env:TITAN_SIM_SCENARIO -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_SIM_SCENARIO = $_previousSimScenario
  }

  if ($null -eq $_previousMaxTicks) {
    Remove-Item Env:TITAN_MAX_TICKS -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_MAX_TICKS = $_previousMaxTicks
  }

  if ($null -eq $_previousTickSeconds) {
    Remove-Item Env:TITAN_TICK_SECONDS -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_TICK_SECONDS = $_previousTickSeconds
  }

  if ($null -eq $_previousReportDir) {
    Remove-Item Env:TITAN_REPORT_DIR -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_REPORT_DIR = $_previousReportDir
  }

  if ($null -eq $_previousLabelMapFile) {
    Remove-Item Env:TITAN_VISION_LABEL_MAP_FILE -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_VISION_LABEL_MAP_FILE = $_previousLabelMapFile
  }

  Pop-Location
}
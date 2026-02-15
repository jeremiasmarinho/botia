param(
  [switch]$HealthOnly,
  [ValidateSet("off", "wait", "fold", "call", "raise", "cycle")]
  [string]$SimScenario = "off"
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

try {
  if ($SimScenario -ne "off") {
    $env:TITAN_SIM_SCENARIO = $SimScenario
    Write-Host "[SIM] TITAN_SIM_SCENARIO=$SimScenario"
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
}
finally {
  if ($null -eq $_previousSimScenario) {
    Remove-Item Env:TITAN_SIM_SCENARIO -ErrorAction SilentlyContinue
  }
  else {
    $env:TITAN_SIM_SCENARIO = $_previousSimScenario
  }

  Pop-Location
}
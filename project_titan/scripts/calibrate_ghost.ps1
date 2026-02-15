param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("manual", "show", "validate", "env")]
  [string]$Mode,

  [string]$ProfileName = "default",
  [string]$Fold,
  [string]$Call,
  [string]$RaiseSmall,
  [string]$RaiseBig,
  [switch]$PowerShell,
  [switch]$Json
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Find-Python {
  $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) { return $venvPython }
  return "python"
}

$python = Find-Python
$script = Join-Path $projectRoot "training\calibrate_ghost.py"

if (-not (Test-Path $script)) {
  throw "Script nao encontrado: $script"
}

$cmdArgs = @($script, $Mode, "--profile", $ProfileName)

switch ($Mode) {
  "manual" {
    if (-not $Fold -or -not $Call -or -not $RaiseSmall -or -not $RaiseBig) {
      throw "Modo manual requer: -Fold x,y -Call x,y -RaiseSmall x,y -RaiseBig x,y"
    }
    $cmdArgs += @("--fold", $Fold, "--call", $Call, "--raise-small", $RaiseSmall, "--raise-big", $RaiseBig)
  }
  "show" {
    if ($Json) { $cmdArgs += "--json" }
  }
  "env" {
    if ($PowerShell) { $cmdArgs += "--powershell" }
  }
}

& $python @cmdArgs
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
  throw "calibrate_ghost.py falhou (exit=$exitCode)"
}

param(
  [string]$Distro = "Ubuntu-24.04"
)

$ErrorActionPreference = "Stop"

function Test-DistroInstalled {
  param([string]$Name)
  $list = wsl -l -q 2>$null
  if (-not $list) { return $false }
  return ($list -split "`r?`n" | ForEach-Object { $_.Trim() }) -contains $Name
}

Write-Host "[1/5] Checking admin privileges..."
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run this script as Administrator."
}

Write-Host "[2/5] Installing WSL distro: $Distro"
wsl --install -d $Distro

if (-not (Test-DistroInstalled -Name $Distro)) {
  throw "Distro '$Distro' ainda não está disponível. Reinicie o Windows e execute este script novamente."
}

Write-Host "[3/5] Ensuring distro starts..."
wsl -d $Distro -- bash -lc "echo WSL ready"

Write-Host "[4/5] Installing Linux toolchain for Buildozer..."
wsl -d $Distro -- bash -lc "sudo apt update && sudo apt install -y python3-pip python3-venv openjdk-17-jdk zip unzip git libffi-dev libssl-dev build-essential ccache && python3 -m pip install --upgrade pip && python3 -m pip install buildozer cython"

Write-Host "[5/5] Toolchain ready. Run scripts/build_apk_wsl.sh next."

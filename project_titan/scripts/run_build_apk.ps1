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

Write-Host "Running APK build inside WSL distro: $Distro"

if (-not (Test-DistroInstalled -Name $Distro)) {
    throw "WSL distro '$Distro' não encontrada. Rode primeiro .\scripts\setup_apk_toolchain.ps1 e reinicie o Windows se solicitado."
}

$bashCmd = @'
set -euo pipefail
cd /mnt/f/botia/project_titan/mobile
python3 -m pip install --upgrade pip
python3 -m pip install buildozer cython
buildozer android debug
'@

wsl -d $Distro -- bash -lc $bashCmd

$apkDir = "F:\botia\project_titan\mobile\bin"
if (-not (Test-Path $apkDir)) {
    throw "Build finalizado sem pasta de saída esperada: $apkDir"
}

Write-Host "APK build finished. Check: $apkDir"

#!/usr/bin/env pwsh
#
# build-native-win.ps1 — Build Rust N-API addon for Windows x64
#
# Prerequisites:
#   1. Rust toolchain: https://rustup.rs (rustup default stable-x86_64-pc-windows-msvc)
#   2. Node.js 18+ (LTS recommended)
#   3. Visual Studio Build Tools 2022 (C++ workload)
#   4. npm install in core-engine (for @napi-rs/cli)
#
# Usage:
#   .\scripts\build-native-win.ps1
#
# Output:
#   titan-distributed/packages/core-engine/titan-core.win32-x64-msvc.node
#   ↳ Copied to titan-edge/native/titan-core.win32-x64-msvc.node

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$CoreEngine = Join-Path $Root "titan-distributed\packages\core-engine"
$EdgeNative = Join-Path $Root "titan-edge\native"

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  TITAN CORE ENGINE - N-API Build (Windows x64 MSVC)" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 0: Verify prerequisites ──────────────────────────────────
Write-Host "[0/5] Checking prerequisites..." -ForegroundColor Yellow

$rustVersion = & rustc --version 2>$null
if (-not $rustVersion) {
  Write-Host "ERROR: Rust not found. Install from https://rustup.rs" -ForegroundColor Red
  exit 1
}
Write-Host "  Rust: $rustVersion" -ForegroundColor Green

$nodeVersion = & node --version 2>$null
if (-not $nodeVersion) {
  Write-Host "ERROR: Node.js not found." -ForegroundColor Red
  exit 1
}
Write-Host "  Node: $nodeVersion" -ForegroundColor Green

$cargoTarget = & rustup show active-toolchain 2>$null
Write-Host "  Toolchain: $cargoTarget" -ForegroundColor Green

# ── Step 1: Install npm deps in core-engine ──────────────────────
Write-Host ""
Write-Host "[1/5] Installing core-engine npm dependencies..." -ForegroundColor Yellow
Push-Location $CoreEngine
try {
  & npm install
  if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
  Write-Host "  npm install: OK" -ForegroundColor Green
}
finally {
  Pop-Location
}

# ── Step 2: Build Rust → .node (release, LTO fat, target-cpu=native) ─
Write-Host ""
Write-Host "[2/5] Building Rust N-API addon (release + LTO)..." -ForegroundColor Yellow
Write-Host "  This may take 2-5 minutes on first build." -ForegroundColor DarkGray
Push-Location $CoreEngine
try {
  $env:RUSTFLAGS = "-C target-cpu=native"
  & npx napi build --release --platform --target x86_64-pc-windows-msvc
  if ($LASTEXITCODE -ne 0) { throw "napi build failed" }
  Write-Host "  napi build: OK" -ForegroundColor Green
}
finally {
  $env:RUSTFLAGS = $null
  Pop-Location
}

# ── Step 3: Verify the build artifact ────────────────────────────
Write-Host ""
Write-Host "[3/5] Verifying build artifact..." -ForegroundColor Yellow
$Artifact = Join-Path $CoreEngine "titan-core.win32-x64-msvc.node"
if (-not (Test-Path $Artifact)) {
  # Fallback: check for non-platform filename
  $FallbackArtifact = Join-Path $CoreEngine "titan_core.node"
  if (Test-Path $FallbackArtifact) {
    $Artifact = $FallbackArtifact
    Write-Host "  Found fallback: titan_core.node" -ForegroundColor Yellow
  }
  else {
    Write-Host "ERROR: Build artifact not found at:" -ForegroundColor Red
    Write-Host "  $Artifact" -ForegroundColor Red
    Write-Host "  $FallbackArtifact" -ForegroundColor Red
    exit 1
  }
}
$size = (Get-Item $Artifact).Length / 1KB
Write-Host "  Artifact: $Artifact ($([math]::Round($size, 0)) KB)" -ForegroundColor Green

# ── Step 4: Copy to titan-edge/native/ ───────────────────────────
Write-Host ""
Write-Host "[4/5] Copying to titan-edge/native/..." -ForegroundColor Yellow
if (-not (Test-Path $EdgeNative)) {
  New-Item -ItemType Directory -Path $EdgeNative -Force | Out-Null
}
Copy-Item $Artifact -Destination $EdgeNative -Force
$DestFile = Join-Path $EdgeNative (Split-Path $Artifact -Leaf)
Write-Host "  Copied: $DestFile" -ForegroundColor Green

# ── Step 5: Quick smoke test ─────────────────────────────────────
Write-Host ""
Write-Host "[5/5] Smoke test (load addon + version check)..." -ForegroundColor Yellow
$nodeFile = $DestFile -replace '\\', '/'
$smokeCode = "try { const a=require('$nodeFile'); if(typeof a.init==='function') a.init(); const v=typeof a.version==='function'?a.version():'unknown'; console.log('VERSION: '+v); const eq=a.equity([48,44,40,36,32],[50,44,38],3000); console.log('EQUITY: '+JSON.stringify(eq)); console.log('SMOKE_TEST: PASS'); } catch(e) { console.error('SMOKE_TEST: FAIL - '+e.message); process.exit(1); }"
$result = & node -e $smokeCode 2>&1
$resultStr = $result -join "`n"
if ($resultStr -match "SMOKE_TEST: PASS") {
  foreach ($line in $result) {
    if ($line -match "VERSION|EQUITY|SMOKE") {
      Write-Host "  $line" -ForegroundColor Green
    }
  }
}
else {
  Write-Host "  SMOKE TEST FAILED:" -ForegroundColor Red
  Write-Host "  $resultStr" -ForegroundColor Red
  exit 1
}

# ── Done ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  BUILD COMPLETE - Ready for Live-Fire Test" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Start LDPlayer with PPPoker" -ForegroundColor Gray
Write-Host "    2. cd titan-edge" -ForegroundColor Gray
Write-Host "    3. npm run live" -ForegroundColor Gray
Write-Host ""

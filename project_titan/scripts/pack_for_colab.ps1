<#
.SYNOPSIS
    Empacota project_titan + datasets num zip pronto para Colab.

.DESCRIPTION
    Cria titan_pacote.zip com a estrutura:
        project_titan/   (código, training/, tools/, etc.)
        datasets/        (synthetic/, titan_cards/)

    Exclui: .venv, __pycache__, runs/, .git, *.pyc, modelos grandes (>50MB)

.EXAMPLE
    .\scripts\pack_for_colab.ps1
    .\scripts\pack_for_colab.ps1 -OutputPath C:\temp\titan_pacote.zip
#>

param(
  [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

# Resolve paths
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RepoRoot = Split-Path -Parent $ProjectRoot

if (-not $OutputPath) {
  $OutputPath = Join-Path $RepoRoot "titan_pacote.zip"
}

Write-Host "[PACK] Project root: $ProjectRoot" -ForegroundColor Cyan
Write-Host "[PACK] Output:       $OutputPath" -ForegroundColor Cyan

# Temporary staging directory
$StagingDir = Join-Path ([System.IO.Path]::GetTempPath()) "titan_pack_$(Get-Random)"
$null = New-Item -ItemType Directory -Path $StagingDir -Force

try {
  # ── 1. Copy project_titan (code only) ──
  Write-Host "[PACK] Copiando project_titan..." -ForegroundColor Yellow
  $ProjectDst = Join-Path $StagingDir "project_titan"

  $excludeDirs = @(".venv", "__pycache__", "runs", ".git", "node_modules", ".pytest_cache", "reports", "data", "datasets")
  $excludeExts = @("*.pyc", "*.pyo")

  # Use robocopy for efficient filtered copy
  $excludeDirArgs = $excludeDirs | ForEach-Object { "/XD"; $_ }
  $excludeFileArgs = $excludeExts | ForEach-Object { "/XF"; $_ }

  & robocopy $ProjectRoot $ProjectDst /E /NFL /NDL /NJH /NJS /NP @excludeDirArgs @excludeFileArgs | Out-Null

  # Remove large model files (keep only small ones)
  Get-ChildItem -Path $ProjectDst -Recurse -Filter "*.pt" | Where-Object { $_.Length -gt 50MB } | ForEach-Object {
    Write-Host "  Excluindo modelo grande: $($_.Name) ($([math]::Round($_.Length/1MB, 1)) MB)" -ForegroundColor DarkYellow
    Remove-Item $_.FullName -Force
  }

  # ── 2. Copy datasets ──
  Write-Host "[PACK] Copiando datasets..." -ForegroundColor Yellow
  $DatasetsSrc = Join-Path $ProjectRoot "datasets"
  $DatasetsDst = Join-Path $StagingDir "datasets"

  # synthetic
  $synthSrc = Join-Path $DatasetsSrc "synthetic"
  if (Test-Path $synthSrc) {
    $synthDst = Join-Path $DatasetsDst "synthetic"
    & robocopy $synthSrc $synthDst /E /NFL /NDL /NJH /NJS /NP | Out-Null
    $trainCount = (Get-ChildItem (Join-Path $synthDst "images\train") -File -ErrorAction SilentlyContinue | Measure-Object).Count
    $valCount = (Get-ChildItem (Join-Path $synthDst "images\val") -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "  synthetic: $trainCount train / $valCount val" -ForegroundColor Green
  }
  else {
    Write-Host "  ⚠ synthetic/ não encontrado!" -ForegroundColor Red
  }

  # titan_cards
  $tcSrc = Join-Path $DatasetsSrc "titan_cards"
  if (Test-Path $tcSrc) {
    $tcDst = Join-Path $DatasetsDst "titan_cards"
    & robocopy $tcSrc $tcDst /E /NFL /NDL /NJH /NJS /NP | Out-Null
    $trainCount = (Get-ChildItem (Join-Path $tcDst "images\train") -File -ErrorAction SilentlyContinue | Measure-Object).Count
    $valCount = (Get-ChildItem (Join-Path $tcDst "images\val") -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "  titan_cards: $trainCount train / $valCount val" -ForegroundColor Green
  }
  else {
    Write-Host "  ⚠ titan_cards/ não encontrado!" -ForegroundColor Red
  }

  # ── 3. Create zip with forward slashes (Linux-compatible) ──
  Write-Host "[PACK] Criando zip (forward slashes para Linux)..." -ForegroundColor Yellow
  if (Test-Path $OutputPath) {
    Remove-Item $OutputPath -Force
  }

  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem

  $zipStream = [System.IO.File]::Create($OutputPath)
  $archive = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)

  $allFiles = Get-ChildItem -Path $StagingDir -Recurse -File
  $fileCount = 0
  foreach ($file in $allFiles) {
    $relativePath = $file.FullName.Substring($StagingDir.Length + 1)
    # Convert backslashes to forward slashes for Linux compatibility
    $entryName = $relativePath.Replace('\', '/')
    $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
    $entryStream = $entry.Open()
    $fileStream = [System.IO.File]::OpenRead($file.FullName)
    $fileStream.CopyTo($entryStream)
    $fileStream.Close()
    $entryStream.Close()
    $fileCount++
  }

  $archive.Dispose()
  $zipStream.Close()

  $sizeMB = [math]::Round((Get-Item $OutputPath).Length / 1MB, 1)
  Write-Host ""
  Write-Host "✅ Pacote criado: $OutputPath ($sizeMB MB) - $fileCount arquivos" -ForegroundColor Green
  Write-Host ""
  Write-Host "Próximos passos:" -ForegroundColor Cyan
  Write-Host "  1. Suba $OutputPath para Google Drive em Titan_Training/" -ForegroundColor White
  Write-Host "  2. Abra training/colab_hybrid_train.ipynb no Colab" -ForegroundColor White
  Write-Host "  3. Execute todas as células" -ForegroundColor White

}
finally {
  # Cleanup staging
  if (Test-Path $StagingDir) {
    Remove-Item $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

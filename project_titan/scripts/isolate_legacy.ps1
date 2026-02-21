# ═══════════════════════════════════════════════════════════════════════
# Titan — Legacy Isolation Script (PowerShell)
# Move Texas Hold'em / PPPoker-specific code to _legacy_holdem
# ═══════════════════════════════════════════════════════════════════════
#
# SAFETY:
#   - Uses git mv so full history is preserved
#   - Run from project_titan/ root
#   - Commit BEFORE and AFTER running this
#
# Usage:
#   cd f:\botia\project_titan
#   git add -A; git commit -m "checkpoint: pre-legacy-isolation"
#   .\scripts\isolate_legacy.ps1
#   git add -A; git commit -m "refactor: isolate legacy HE/mouse code to _legacy_holdem"
# ═══════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$LEGACY = "_legacy_holdem"

Write-Host "═══ Titan Legacy Isolation ═══" -ForegroundColor Cyan
Write-Host "Target: $LEGACY/" -ForegroundColor Gray
Write-Host ""

# ── Create archive structure ────────────────────────────────────────
$dirs = @(
  "$LEGACY/agent",
  "$LEGACY/simulator/logic",
  "$LEGACY/tools",
  "$LEGACY/training",
  "$LEGACY/tests",
  "$LEGACY/scripts",
  "$LEGACY/configs",
  "$LEGACY/assets"
)
foreach ($d in $dirs) {
  New-Item -ItemType Directory -Path $d -Force | Out-Null
}

# ── Helper: git mv with fallback ───────────────────────────────────
function Move-Legacy {
  param([string]$Source, [string]$Dest)
  if (Test-Path $Source) {
    try {
      git mv $Source $Dest 2>$null
    }
    catch {
      Move-Item -Path $Source -Destination $Dest -Force
    }
    Write-Host "  ✓ $Source → $Dest" -ForegroundColor Green
  }
  else {
    Write-Host "  ⊘ $Source (not found, skipping)" -ForegroundColor Yellow
  }
}

# ── 🔴 TOXIC: Hard HE logic / PPPoker-only ─────────────────────────

Write-Host "[1/8] Agent — 2-card mock vision..." -ForegroundColor Red
Move-Legacy "agent/vision_mock.py" "$LEGACY/agent/"

Write-Host "[2/8] Simulator — HE decision server..." -ForegroundColor Red
Move-Legacy "simulator/logic/decision_server.py" "$LEGACY/simulator/logic/"

Write-Host "[3/8] Tools — PPPoker card reader..." -ForegroundColor Red
Move-Legacy "tools/card_reader.py" "$LEGACY/tools/"

Write-Host "[4/8] Training — PPPoker data generator..." -ForegroundColor Red
Move-Legacy "training/generate_pppoker_data.py" "$LEGACY/training/"

Write-Host "[5/8] Tests — PPPoker card reader tests..." -ForegroundColor Red
Move-Legacy "tests/test_card_reader.py" "$LEGACY/tests/"

Write-Host "[6/8] Scripts — PPPoker test script..." -ForegroundColor Red
Move-Legacy "scripts/test_card_reader.py" "$LEGACY/scripts/"

Write-Host "[7/8] Configs — PPPoker pixel coordinates..." -ForegroundColor Red
Move-Legacy "config_club.yaml" "$LEGACY/configs/"
Move-Legacy "config_calibration.yaml" "$LEGACY/configs/"

Write-Host "[8/8] Assets — PPPoker backgrounds..." -ForegroundColor Red
if (Test-Path "assets/backgrounds") {
  try {
    git mv "assets/backgrounds" "$LEGACY/assets/" 2>$null
  }
  catch {
    Move-Item -Path "assets/backgrounds" -Destination "$LEGACY/assets/" -Force
  }
  Write-Host "  ✓ assets/backgrounds/ → $LEGACY/assets/" -ForegroundColor Green
}

# ── Create README ───────────────────────────────────────────────────
$readmeContent = @"
# _legacy_holdem — Archived Code

**Archived:** $(Get-Date -Format "yyyy-MM-dd")

This folder contains code that is incompatible with the Titan Edge AI
(Electron + ADB + PLO5/PLO6) architecture:

| File | Reason |
|------|--------|
| ``agent/vision_mock.py`` | Hardcoded 2-card (HE) hero hands |
| ``simulator/logic/decision_server.py`` | ``sample(deck.cards, 2)`` — HE villain hands |
| ``tools/card_reader.py`` | PPPoker-specific HSV colors + gold border detection |
| ``training/generate_pppoker_data.py`` | PPPoker visual style (gold borders, table green) |
| ``tests/test_card_reader.py`` | Tests for PPPoker card reader |
| ``scripts/test_card_reader.py`` | PPPoker hardcoded button positions |
| ``configs/config_club.yaml`` | PPPoker pixel-level button coordinates |
| ``configs/config_calibration.yaml`` | PPPoker resolution-specific coords |
| ``assets/backgrounds/`` | PPPoker table screenshots |

**DO NOT DELETE** — preserved for reference during migration.
These files have full git history via ``git log --follow``.
"@

Set-Content -Path "$LEGACY/README.md" -Value $readmeContent -Encoding UTF8

Write-Host ""
Write-Host "═══ Isolation Complete ═══" -ForegroundColor Cyan
Write-Host ""
Write-Host "Files moved to ${LEGACY}/:" -ForegroundColor Gray
Get-ChildItem -Path $LEGACY -Recurse -File | ForEach-Object {
  Write-Host "  $_" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  git add -A" -ForegroundColor White
Write-Host '  git commit -m "refactor: isolate legacy HE/PPPoker code to _legacy_holdem"' -ForegroundColor White
Write-Host ""
Write-Host "⚠️  Imports that reference moved files will break." -ForegroundColor Yellow
Write-Host "   Search for: vision_mock, decision_server, card_reader, generate_pppoker" -ForegroundColor DarkYellow

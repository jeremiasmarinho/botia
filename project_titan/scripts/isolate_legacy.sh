#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# Titan — Legacy Isolation Script
# Move Texas Hold'em / Python-mouse / PPPoker-specific code to _legacy
# ═══════════════════════════════════════════════════════════════════════
#
# SAFETY:
#   • Uses git mv so full history is preserved
#   • Run from project_titan/ root
#   • Commit BEFORE and AFTER running this
#
# Usage:
#   cd f:/botia/project_titan
#   git add -A && git commit -m "checkpoint: pre-legacy-isolation"
#   bash scripts/isolate_legacy.sh
#   git add -A && git commit -m "refactor: isolate legacy HE/mouse code to _legacy_holdem"
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

LEGACY="_legacy_holdem"

echo "═══ Titan Legacy Isolation ═══"
echo "Target: $LEGACY/"
echo ""

# ── Create archive structure ────────────────────────────────────────
mkdir -p "$LEGACY/agent"
mkdir -p "$LEGACY/simulator/logic"
mkdir -p "$LEGACY/tools"
mkdir -p "$LEGACY/training"
mkdir -p "$LEGACY/tests"
mkdir -p "$LEGACY/scripts"
mkdir -p "$LEGACY/configs"
mkdir -p "$LEGACY/assets"

# ── 🔴 TOXIC: Hard HE logic / PPPoker-only ─────────────────────────

echo "[1/8] Moving TOXIC agent files..."
git mv agent/vision_mock.py         "$LEGACY/agent/"     2>/dev/null || mv agent/vision_mock.py "$LEGACY/agent/"

echo "[2/8] Moving TOXIC simulator files..."
git mv simulator/logic/decision_server.py "$LEGACY/simulator/logic/" 2>/dev/null || mv simulator/logic/decision_server.py "$LEGACY/simulator/logic/"

echo "[3/8] Moving TOXIC tools..."
git mv tools/card_reader.py         "$LEGACY/tools/"     2>/dev/null || mv tools/card_reader.py "$LEGACY/tools/"

echo "[4/8] Moving TOXIC training data generator..."
git mv training/generate_pppoker_data.py "$LEGACY/training/" 2>/dev/null || mv training/generate_pppoker_data.py "$LEGACY/training/"

echo "[5/8] Moving TOXIC tests..."
git mv tests/test_card_reader.py    "$LEGACY/tests/"     2>/dev/null || mv tests/test_card_reader.py "$LEGACY/tests/"

echo "[6/8] Moving TOXIC scripts..."
git mv scripts/test_card_reader.py  "$LEGACY/scripts/"   2>/dev/null || mv scripts/test_card_reader.py "$LEGACY/scripts/"

echo "[7/8] Moving TOXIC configs (pixel coords)..."
git mv config_club.yaml             "$LEGACY/configs/"   2>/dev/null || mv config_club.yaml "$LEGACY/configs/"
git mv config_calibration.yaml      "$LEGACY/configs/"   2>/dev/null || mv config_calibration.yaml "$LEGACY/configs/"

echo "[8/8] Moving TOXIC assets (PPPoker backgrounds)..."
git mv assets/backgrounds           "$LEGACY/assets/"    2>/dev/null || mv assets/backgrounds "$LEGACY/assets/"

# ── Create README in legacy folder ──────────────────────────────────
cat > "$LEGACY/README.md" << 'EOF'
# _legacy_holdem — Archived Code

**Archived:** $(date +%Y-%m-%d)

This folder contains code that is incompatible with the Titan Edge AI
(Electron + ADB + PLO5/PLO6) architecture:

| File | Reason |
|------|--------|
| `agent/vision_mock.py` | Hardcoded 2-card (HE) hero hands |
| `simulator/logic/decision_server.py` | `sample(deck.cards, 2)` — HE villain hands |
| `tools/card_reader.py` | PPPoker-specific HSV colors + gold border detection |
| `training/generate_pppoker_data.py` | PPPoker visual style (gold borders, table green) |
| `tests/test_card_reader.py` | Tests for PPPoker card reader |
| `scripts/test_card_reader.py` | PPPoker hardcoded button positions |
| `configs/config_club.yaml` | PPPoker pixel-level button coordinates |
| `configs/config_calibration.yaml` | PPPoker resolution-specific coords |
| `assets/backgrounds/` | PPPoker table screenshots |

**DO NOT DELETE** — preserved for reference during migration.
These files have full git history via `git log --follow`.
EOF

echo ""
echo "═══ Isolation Complete ═══"
echo ""
echo "Files moved to $LEGACY/:"
find "$LEGACY" -type f | sort
echo ""
echo "Next steps:"
echo "  git add -A"
echo "  git commit -m 'refactor: isolate legacy HE/PPPoker code to _legacy_holdem'"
echo ""
echo "⚠️  Imports that reference moved files will break."
echo "    Search for: vision_mock, decision_server, card_reader, generate_pppoker"
echo "    These should only be referenced by other TOXIC/RECYCLABLE modules."

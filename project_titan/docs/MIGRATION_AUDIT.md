# Project Titan — Comprehensive Migration Audit

> **Scope**: Every `.py`, `.yaml`, `.bat` source file under `project_titan/`  
> **Goal**: Classify each module for PLO5/PLO6 migration across 7 dimensions  
> **Date**: Auto-generated audit  

---

## Legend — 7 Audit Dimensions

| Dim | Tag | Meaning |
|-----|-----|---------|
| 1 | **HOLDEM** | Contains Texas Hold'em (2-card) specific logic that must change for PLO |
| 2 | **MOUSE** | Controls mouse/keyboard via PyAutoGUI or similar |
| 3 | **ADB** | Uses Android Debug Bridge |
| 4 | **AGNOSTIC** | Format-agnostic — works for any poker variant without changes |
| 5 | **SCREEN** | Screen capture via win32gui / mss / pygetwindow |
| 6 | **SQLITE** | Uses or defines SQLite schemas |
| 7 | **TRAINING** | Training pipeline / dataset assets |

### Migration Priority Codes

- ✅ **Ready** — No changes needed for PLO5/PLO6
- ⚠️ **Review** — Minor adjustments may be needed (mock data, comments, constants)
- 🔴 **Change Required** — Hard-coded Hold'em logic that must be rewritten

---

## Executive Summary

| Metric | Count |
|--------|-------|
| Total Python source files audited | **~80** |
| Config/YAML files | **4** |
| Batch scripts | **1** |
| Format-agnostic (no changes needed) | **~65** |
| Minor review needed | **~8** |
| Hard Hold'em dependency (change required) | **2** |

### Key Findings

1. **`core/math_engine.py` already supports PLO (3–6 cards)**. The `_evaluate_omaha_like()` method correctly enumerates all 2-from-hand + 3-from-board combinations.
2. **`tools/vision_tool.py` already caps hero cards at 6** and includes sim scenarios with 4-card and 6-card hands.
3. **Only 2 files contain hard Hold'em-specific logic**: `simulator/logic/decision_server.py` (always deals 2-card villain hands) and `agent/vision_mock.py` (2-card mock hero hands).
4. **No ADB usage found anywhere** in the codebase. The mobile PoC (`mobile/main.py`) uses Kivy, not ADB.
5. **Training pipeline is already PLO-ready**: `generate_pppoker_data.py` generates 4–6 card hero hands and PLO5/PLO6 opponent showdown scenarios.

---

## Per-File Classification

### `agent/` — Agent Layer (10 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `poker_agent.py` | 695 | AGNOSTIC | ✅ Ready | Orchestrates vision→equity→action→memory. No variant-specific logic. ZMQ check-in with HiveBrain. |
| `ghost_mouse.py` | 569 | MOUSE, AGNOSTIC | ✅ Ready | Humanized Bézier mouse via PyAutoGUI. Log-normal click holds, micro-overshoots, Poisson delays. No poker logic. |
| `vision_yolo.py` | 871 | SCREEN, AGNOSTIC | ✅ Ready | `EmulatorWindow` finds LDPlayer9 via `win32gui.EnumWindows`. Captures ROI via `mss`. Runs YOLO inference. No card-count assumptions. |
| `vision_ocr.py` | ~180 | SCREEN, AGNOSTIC | ✅ Ready | `TitanOCR` reads numeric values (pot/stack/call) from screen crops. No poker-variant logic. |
| `vision_mock.py` | ~130 | HOLDEM | ⚠️ Review | Mock scenario data uses 2-card hero hands (e.g. `["As", "Kd"]`). Update mock scenarios to include 4–6 card hands. |
| `calibration.py` | ~240 | AGNOSTIC | ✅ Ready | Action-button calibration cache with EMA smoothing, JSON persistence. |
| `agent_config.py` | ~95 | AGNOSTIC | ✅ Ready | `AgentConfig` dataclass + env-var parsing. |
| `sanity_guard.py` | ~85 | AGNOSTIC | ✅ Ready | OCR value-stability validation. |
| `zombie_agent.py` | ~30 | AGNOSTIC | ✅ Ready | Stateless agent wrapper. |
| `__init__.py` | 1 | AGNOSTIC | ✅ Ready | Empty. |

### `core/` — Core Engine (3 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `math_engine.py` | ~150 | **AGNOSTIC** | ✅ Ready | **Already PLO-ready.** `_evaluate_omaha_like()` handles 3–6 card hands using exact Omaha rules (2-from-hand + 3-from-board). Falls back to Hold'em eval for 2-card hands. Uses `treys` library. |
| `hive_brain.py` | ~300 | AGNOSTIC | ✅ Ready | ZMQ REP coordinator for multi-agent. Shares dead cards, collusion obfuscation. Card passthrough — no count validation. |
| `rng_auditor.py` | ~175 | AGNOSTIC | ✅ Ready | Z-score test for detecting super-user opponents. Pure statistical — no poker-variant logic. |

### `memory/` — Persistence (2 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `opponent_db.py` | ~350 | SQLITE, AGNOSTIC | ✅ Ready | SQLite `opponent_stats` table: VPIP, PFR, aggression, fold_to_3bet, cbet_freq, showdown_freq, avg_bet_sizing. Opponent classification (Fish/Nit/LAG/TAG). **Schema is variant-agnostic** — stats are ratios, not card-count dependent. |
| `redis_memory.py` | ~120 | AGNOSTIC | ✅ Ready | Dual-backend KV store (Redis with in-memory dict fallback). |

### `orchestrator/` — Runtime Orchestration (3 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `engine.py` | ~250 | AGNOSTIC | ✅ Ready | Tick-loop orchestrator with telemetry. |
| `registry.py` | ~40 | AGNOSTIC | ✅ Ready | Simple DI container. |
| `healthcheck.py` | ~25 | AGNOSTIC | ✅ Ready | Single-step health check. |

### `utils/` — Shared Utilities (5 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `card_utils.py` | ~125 | AGNOSTIC | ✅ Ready | 52-card deck encoding, normalization, Portuguese display names, `street_from_board()`. Standard deck — not variant-specific. |
| `config.py` | ~175 | AGNOSTIC | ✅ Ready | Runtime config dataclasses (`VisionRuntimeConfig`, `OCRRuntimeConfig`). |
| `logger.py` | ~175 | AGNOSTIC | ✅ Ready | ANSI-colored terminal + JSONL file logger. |
| `titan_config.py` | ~175 | AGNOSTIC | ✅ Ready | YAML config loader with env-var override (`TITAN_*`). |
| `__init__.py` | 1 | AGNOSTIC | ✅ Ready | Empty. |

### `workflows/` — Decision Logic (5 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `poker_hand_workflow.py` | 758 | AGNOSTIC | ⚠️ Review | Decision orchestrator. `information_quality()` uses `observed / 12.0` denominator — calibrated for PLO6 (6 hero + 5 board + 1 dead = 12). **Correct for PLO6, may need adjustment for PLO5 (11) or Hold'em (10).** `_calculate_raise_amount()` comments reference PLO6 calibration. Otherwise fully variant-agnostic (equity-based). |
| `gto_engine.py` | 574 | AGNOSTIC | ✅ Ready | Mixed-strategy engine with sigmoid thresholds, opponent adaptation. Operates on equity values, not card counts. |
| `thresholds.py` | ~250 | AGNOSTIC | ✅ Ready | Deterministic equity threshold ladder. Operates on equity + pot odds. |
| `protocol.py` | ~30 | AGNOSTIC | ✅ Ready | `SupportsMemory` protocol definition. |
| `__init__.py` | ~14 | AGNOSTIC | ✅ Ready | Package imports. |

### `tools/` — Vision & Action Tools (17 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `vision_tool.py` | 839 | SCREEN, AGNOSTIC | ✅ Ready | Main vision pipeline. `hero_cards` dedupe capped at `max_size=6` (PLO6 ready). `board_cards` capped at 5. Sim scenarios include 4-card and 6-card hero hands. |
| `vision_models.py` | 63 | AGNOSTIC | ✅ Ready | `TableSnapshot` and `DetectionItem` dataclasses. |
| `vision_constants.py` | 63 | AGNOSTIC | ✅ Ready | Card token constants (standard 52-card deck). |
| `vision_label_parser.py` | 515 | AGNOSTIC | ✅ Ready | YOLO label parsing pipeline. Category classification for hero/board/dead/pot/stack. |
| `card_reader.py` | 792 | SCREEN, AGNOSTIC | ✅ Ready | `PPPokerCardReader` — OCR + HSV colour analysis fallback. No hand-size limit. Region estimation from button positions. |
| `terminator_vision.py` | 656 | SCREEN, AGNOSTIC | ✅ Ready | Real-time OpenCV debug overlay ("Visão do Exterminador"). Displays any cards detected. |
| `visual_overlay.py` | ~340 | AGNOSTIC | ✅ Ready | Standalone overlay drawing functions. |
| `visual_calibrator.py` | ~490 | SCREEN, AGNOSTIC | ✅ Ready | Interactive OpenCV calibration for hero_area, board_area, pot_region, button positions. Saves to YAML. |
| `card_annotator.py` | 539 | AGNOSTIC, TRAINING | ✅ Ready | Interactive YOLO card annotation. 62-class scheme (52 cards + 10 UI). |
| `auto_labeler.py` | ~280 | AGNOSTIC, TRAINING | ✅ Ready | Auto-generates YOLO labels from config coordinates. |
| `label_assist.py` | ~245 | AGNOSTIC, TRAINING | ✅ Ready | Annotation workspace preparation (MD5 dedup, manifest CSV). |
| `action_tool.py` | ~250 | MOUSE, AGNOSTIC | ✅ Ready | Bridge to `GhostMouse`. Two-step PPPoker raise flow (Raise→preset→confirm). UI-specific but not poker-variant-specific. |
| `equity_tool.py` | ~60 | AGNOSTIC | ✅ Ready | Thin wrapper over `MathEngine`. Supports 2–6 hole cards. |
| `rng_tool.py` | ~160 | AGNOSTIC | ✅ Ready | Showdown ingestion + evasion checks. |
| `diagnose_vision.py` | ~175 | SCREEN, AGNOSTIC | ✅ Ready | Diagnostic script for vision pipeline. |
| `e2e_runner.py` | ~370 | AGNOSTIC | ✅ Ready | E2E test runner with sim/real modes. |
| `smoke_e2e.py` | ~200 | AGNOSTIC | ✅ Ready | E2E smoke test. |

### `simulator/` — Simulation Layer (4 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `__init__.py` | 1 | AGNOSTIC | ✅ Ready | Docstring only. |
| `debug/debug_interface.py` | ~65 | SCREEN, AGNOSTIC | ✅ Ready | Crosshair debug overlay (mss + pyautogui + cv2). |
| `logic/decision_server.py` | ~130 | **HOLDEM** | 🔴 **Change Required** | HTTP equity server. **Hard-codes `sample(deck.cards, 2)` for villain hands** — always deals 2-card villain hands. Must be updated to deal 4–6 card villain hands matching the game variant. |
| `vision/abstract_vision.py` | ~130 | SCREEN, AGNOSTIC | ✅ Ready | Generic YOLO + window capture loop (pygetwindow + mss). |

### `scripts/` — CLI Utilities (5 files)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `calibrate_ocr.py` | ~130 | SCREEN, AGNOSTIC | ✅ Ready | OCR region calibration utility. |
| `human_mouse_demo.py` | ~90 | MOUSE, AGNOSTIC | ✅ Ready | GhostMouse visual demo showing Bézier curves. |
| `live_demo.py` | ~260 | SCREEN, AGNOSTIC | ✅ Ready | Real-time YOLO detection display with OpenCV. |
| `test_card_reader.py` | ~190 | SCREEN, AGNOSTIC | ✅ Ready | PPPoker card reader diagnostic. |
| `vision_profile.py` | ~200 | SCREEN, AGNOSTIC | ✅ Ready | Vision profiling CLI — measures YOLO inference latency and throughput. |

### `training/` — ML Training Pipeline (7 files + 1 YAML)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `train_yolo.py` | ~175 | TRAINING, AGNOSTIC | ✅ Ready | YOLO training script. No variant-specific logic. |
| `prepare_dataset.py` | 368 | TRAINING, AGNOSTIC | ✅ Ready | Dataset organiser (split/validate/stats). 62-class scheme. |
| `evaluate_yolo.py` | ~180 | TRAINING, AGNOSTIC | ✅ Ready | Model evaluation + latency benchmark. |
| `generate_synthetic_data.py` | 743 | TRAINING, AGNOSTIC | ✅ Ready | Generic synthetic card data generator (2–9 cards per image). |
| `generate_pppoker_data.py` | 1228 | TRAINING, AGNOSTIC | ✅ Ready | **Already PLO-ready.** Generates gold-bordered hero cards (4–6), PLO5/PLO6 showdown scenarios with 40–60% card overlap, perspective warp per seat, domain randomization. |
| `capture_frames.py` | ~200 | SCREEN, TRAINING, AGNOSTIC | ✅ Ready | PPPoker screen capture for annotation. |
| `calibrate_ghost.py` | 306 | MOUSE, AGNOSTIC | ✅ Ready | Interactive GhostMouse calibration tool. |
| `smoke_training.py` | ~190 | TRAINING | ⚠️ Review | Smoke test hardcodes `nc=58` assertion — **data.yaml has `nc=62`**. This is a pre-existing discrepancy (smoke test would fail). The 62-class scheme is correct. |
| `data.yaml` | ~95 | TRAINING, AGNOSTIC | ✅ Ready | 62-class YOLO config. Comments explicitly reference PLO6. |

### `mobile/` — Mobile PoC (1 file)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `main.py` | ~45 | AGNOSTIC | ✅ Ready | Kivy offline demo UI. **No ADB usage.** Simulates a decision, does not automate third-party apps. |

### Top-Level Files

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `run_titan.py` | 543 | SCREEN, MOUSE, AGNOSTIC | ✅ Ready | Startup orchestrator. Checks deps → Redis → LDPlayer → HiveBrain thread → Agent subprocess(es). References "PLO6" in banner. No variant-specific logic. |
| `titan_control.py` | 1080 | SCREEN, MOUSE, AGNOSTIC | ✅ Ready | Interactive terminal cockpit (menu). Launches training, diagnostics, overlays, calibrators. No variant logic. |
| `start_squad.bat` | 37 | AGNOSTIC | ✅ Ready | Batch launcher for HiveBrain + 2 agents + orchestrator. |

### Config Files

| File | Dims | Status | Notes |
|------|------|--------|-------|
| `config.yaml` | AGNOSTIC | ✅ Ready | Central config: poker strategy, vision, OCR, GhostMouse, overlay, agent, logging. No variant-specific params. |
| `config_club.yaml` | AGNOSTIC | ✅ Ready | Club-specific overrides (model path, calibrated coordinates, OCR regions). |
| `config_calibration.yaml` | AGNOSTIC | ✅ Ready | Calibration quickstart baseline (1920×1080 reference). |
| `requirements.txt` | — | ✅ Ready | Dependencies: numpy, scipy, pyzmq, redis, ultralytics, mss, pyautogui, pywin32, opencv-python, treys, pyyaml, pytest, pytesseract. |

### `tests/` — Test Suite (14 files + conftest)

| File | Lines | Dims | Status | Notes |
|------|-------|------|--------|-------|
| `conftest.py` | ~10 | AGNOSTIC | ✅ Ready | Path setup only. |
| `test_math_engine.py` | ~40 | AGNOSTIC | ✅ Ready | Tests `MathEngine` with 6-card hero hands (PLO6 test data). |
| `test_poker_hand_workflow.py` | ~130 | AGNOSTIC | ✅ Ready | Tests `PokerHandWorkflow` with 6-card hero hands. Tests commitment, God Mode, dead cards, EV decisions. |
| `test_hive_brain.py` | ~40 | AGNOSTIC | ✅ Ready | Tests HiveBrain with 6-card hands. Tests solo→squad transition, card normalization. |
| `test_card_utils.py` | ~120 | AGNOSTIC | ✅ Ready | Tests card normalization, encoding, Portuguese names, street detection. |
| `test_vision_yolo.py` | ~30 | AGNOSTIC | ✅ Ready | Tests `EmulatorWindow` chrome removal and coordinate conversion. |
| `test_ghost_mouse_path.py` | ~45 | AGNOSTIC | ✅ Ready | Tests Bézier path curvature and step-size variance. |
| `test_gto_opponent_ghost.py` | 439 | AGNOSTIC | ✅ Ready | Comprehensive GTO engine tests (determinism, position, bluff injection, opponent adaptation). OpponentDB tests (CRUD, classification). GhostMouse humanization tests (ease curves, Poisson delays, idle jitter). |
| `test_opponent_db_concurrency.py` | 311 | SQLITE, AGNOSTIC | ✅ Ready | Stress test: 4 concurrent writers (mirrors multi-LDPlayer production). Validates hand counts, latency bounds, read-under-write. |
| `test_redis_memory.py` | ~80 | AGNOSTIC | ✅ Ready | In-memory backend tests (set/get/TTL/delete/keys). |
| `test_rng_auditor.py` | ~80 | AGNOSTIC | ✅ Ready | Z-score super-user detection tests. |
| `test_thresholds.py` | ~55 | AGNOSTIC | ✅ Ready | Information quality and action selection tests. |
| `test_thresholds_potodds.py` | ~100 | AGNOSTIC | ✅ Ready | Pot odds direction tests, edge cases. |
| `test_card_reader.py` | 242 | AGNOSTIC | ✅ Ready | PPPokerCardReader unit tests (suit colour detection, OCR rank parsing, synthetic frame integration). |

---

## Dimension Summary Tables

### 1. Texas Hold'em Specific (HOLDEM) — Changes Required

| File | Issue | Severity | Migration Action |
|------|-------|----------|-----------------|
| `simulator/logic/decision_server.py` | `sample(deck.cards, 2)` — always deals 2-card villain hands | 🔴 Critical | Accept `num_hole_cards` parameter (default from game config). Deal `n` cards per villain instead of hard-coded 2. |
| `agent/vision_mock.py` | Mock scenarios use 2-card hero: `["As", "Kd"]` | ⚠️ Low | Add PLO4/PLO5/PLO6 mock scenarios alongside existing ones. |
| `training/smoke_training.py` | Asserts `nc == 58` but `data.yaml` has `nc = 62` | ⚠️ Low | Update assertion to `nc == 62` (pre-existing bug, not Hold'em-specific). |

### 2. Mouse/Keyboard Control (MOUSE)

| File | Library | Usage |
|------|---------|-------|
| `agent/ghost_mouse.py` | `pyautogui` | Bézier mouse movement, click, hold. Gated by `TITAN_GHOST_MOUSE=1` env var. |
| `tools/action_tool.py` | via `GhostMouse` | Two-step PPPoker raise flow: click Raise → select preset → confirm. |
| `scripts/human_mouse_demo.py` | `pyautogui` | Visual demo (no game interaction). |
| `training/calibrate_ghost.py` | `pyautogui` | Interactive coordinate capture for button calibration. |
| `simulator/debug/debug_interface.py` | `pyautogui` | Crosshair debug overlay (cursor position). |

### 3. ADB Usage

**None found.** The mobile PoC uses Kivy (native Python GUI). All PC automation uses `win32gui` + `mss` + `pyautogui` targeting the LDPlayer9 emulator window directly.

### 4. Screen Capture (SCREEN)

| File | Method | Target |
|------|--------|--------|
| `agent/vision_yolo.py` | `win32gui.EnumWindows` + `mss` | LDPlayer9 emulator window |
| `tools/vision_tool.py` | `mss` | Direct monitor region capture |
| `tools/card_reader.py` | receives frame from vision pipeline | — |
| `tools/terminator_vision.py` | receives frame, renders via OpenCV | — |
| `tools/visual_calibrator.py` | `mss` / static image | Interactive calibration |
| `tools/diagnose_vision.py` | via `VisionTool` | Diagnostic |
| `scripts/calibrate_ocr.py` | `mss` | OCR region calibration |
| `scripts/live_demo.py` | `win32gui` + `mss` | Real-time YOLO display |
| `training/capture_frames.py` | `mss` | Frame capture for annotation |
| `simulator/debug/debug_interface.py` | `mss` | Crosshair overlay |
| `simulator/vision/abstract_vision.py` | `pygetwindow` + `mss` | Generic YOLO window capture |

### 5. SQLite Schemas (SQLITE)

| File | Table | Columns |
|------|-------|---------|
| `memory/opponent_db.py` | `opponent_stats` | `player_id` (PK), `hands_observed`, `vpip_count`, `pfr_count`, `voluntary_count`, `aggression_bets`, `aggression_calls`, `fold_to_3bet_count`, `face_3bet_count`, `cbet_count`, `cbet_opportunity`, `showdown_count`, `showdown_opportunity`, `bet_sizing_sum`, `bet_sizing_count`, `last_seen` |

**Schema is variant-agnostic.** All statistics are ratios (VPIP = voluntary_count/hands_observed) — no card-count dependency.

### 6. Training Pipeline (TRAINING)

| Component | PLO Status | Notes |
|-----------|-----------|-------|
| `data.yaml` | ✅ PLO-ready | 62-class scheme. Comments reference PLO6. |
| `generate_pppoker_data.py` | ✅ PLO-ready | Hero: 4–6 cards with gold borders. Showdown: PLO5/PLO6 with 40–60% overlap, per-seat perspective warp. Domain randomization for sim2real. |
| `generate_synthetic_data.py` | ✅ PLO-ready | Generic generator, 2–9 cards per image. |
| `train_yolo.py` | ✅ PLO-ready | Standard ultralytics training wrapper. |
| Models: `titan_v1.pt`, `titan_v6_final_100ep.pt`, `titan_v7_hybrid.pt` | ✅ | Trained on 62-class scheme. |
| Card assets: `assets/cards/` | ✅ | Standard 52-card PNGs. |

---

## Migration Checklist

### Must-Do (2 items)

- [ ] **`simulator/logic/decision_server.py`** — Replace `sample(deck.cards, 2)` with `sample(deck.cards, num_hole_cards)` where `num_hole_cards` comes from request payload or config (4 for PLO4, 5 for PLO5, 6 for PLO6).
- [ ] **`agent/vision_mock.py`** — Add mock scenarios with 4, 5, and 6 hero cards.

### Should-Do (3 items)

- [ ] **`workflows/poker_hand_workflow.py`** — Review `information_quality()` denominator. Currently `observed / 12.0` — works for PLO6 (6+5+1=12). For PLO5 use 11, for PLO4 use 10. Consider making it config-driven: `max_observable = hero_count + 5 + 1`.
- [ ] **`training/smoke_training.py`** — Fix `nc == 58` assertion to `nc == 62` (pre-existing bug unrelated to migration).
- [ ] **Config files** — Add a `poker.variant` or `poker.hole_cards` setting (e.g., `hole_cards: 6`) so components can query the expected hand size.

### Nice-to-Have (2 items)

- [ ] **`tools/vision_tool.py`** — The `hero_cards` dedup cap is already 6. If PLO4 is supported, consider making this configurable.
- [ ] **Tests** — All test mocks already use 6-card hero hands. Add explicit PLO4/PLO5 test scenarios for edge-case coverage.

---

## Dependency Map

```
pyautogui ─────► agent/ghost_mouse.py ─► tools/action_tool.py
                                        ─► training/calibrate_ghost.py
                                        ─► scripts/human_mouse_demo.py

win32gui + mss ► agent/vision_yolo.py ─► tools/vision_tool.py
                                        ─► scripts/live_demo.py
                                        ─► training/capture_frames.py

ultralytics ───► tools/vision_tool.py
               ► training/train_yolo.py
               ► training/evaluate_yolo.py

treys ─────────► core/math_engine.py ──► tools/equity_tool.py

pytesseract ───► tools/card_reader.py
               ► agent/vision_ocr.py

pyzmq ─────────► core/hive_brain.py ──► agent/poker_agent.py

redis ─────────► memory/redis_memory.py

sqlite3 ───────► memory/opponent_db.py

kivy ──────────► mobile/main.py (standalone, no cross-dependency)
```

---

## Architecture Notes

- **No ADB anywhere** — All automation is PC-hosted via `win32gui` (window find) + `mss` (pixel capture) + `pyautogui` (mouse input). The emulator's Android guest is never directly controlled.
- **PLO evaluation is the standard Omaha approach** — `itertools.combinations(hand, 2)` × `itertools.combinations(board, 3)`, evaluate best 5-card hand. Already implemented in `math_engine.py`.
- **Vision pipeline has no card-count gate** — YOLO detects individual cards by class (0–51). The Y-coordinate thresholding assigns cards to hero vs board zones. The number of detected cards is unconstrained.
- **GhostMouse is fully decoupled** — Receives `(action, coordinates)` from the action tool. The action tool maps action names to button coordinates from config. No poker logic in the mouse layer.
- **Opponent DB is pure statistics** — VPIP, PFR, aggression are ratios. Classification (Fish/Nit/LAG/TAG) uses universal poker thresholds that apply to all variants.

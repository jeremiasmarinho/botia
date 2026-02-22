# Vision Architecture — Project Titan (Shadow Mode)

> Last updated: 2025-02-16

## Overview

Titan's vision pipeline runs **zero-copy WebGPU YOLO inference** inside a
hidden Electron `BrowserWindow`.  A screen capture stream (Windows
Graphics Capture → LDPlayer9 emulator surface) feeds raw frames to
TensorFlow.js, which runs YOLOv8 on the GPU without any CPU round-trip.
Detections flow back to the main process via IPC, where the **GameLoop
state machine** decides whether to act.

```
 LDPlayer9 (PPPoker)
       │  WGC capture (600×1012)
       ▼
 ┌─────────────────────────────────────────────────┐
 │  Hidden BrowserWindow (Chromium + WebGPU)       │
 │                                                 │
 │  ┌──────────┐  ┌───────────┐  ┌──────┐  ┌───┐  │
 │  │ WGC      │→ │ Letterbox │→ │ YOLO │→ │NMS│  │
 │  │ 600×1012 │  │ 640×640   │  │ TF.js│  │   │  │
 │  └──────────┘  └───────────┘  └──────┘  └─┬─┘  │
 │                                           │     │
 │          ipcRenderer.send('vision:detections')  │
 └───────────────────────────────────────────┼─────┘
                                             ▼
 ┌─────────────────────────────────────────────────┐
 │  Main Process (Node.js)                         │
 │  GameLoop → SolverBridge → GtoEngine → ADB tap  │
 └─────────────────────────────────────────────────┘
```

---

## 1. Letterbox Preprocessing

### The Problem

The LDPlayer9 emulator surface is **600 × 1012** pixels.  The YOLO model
expects a square **640 × 640** input.  The naïve approach:

```js
// ❌ BAD — stretches the image, distorts cards
ctx.drawImage(videoEl, 0, 0, 640, 640);
```

This squished a 600:1012 (≈ 3:5) aspect ratio into a 1:1 square,
compressing the Y axis by 37%.  Card shapes became unrecognizable and
only `stack` (a square element) was detected.

### The Fix — Aspect-Preserving Letterbox

```js
// ✅ GOOD — preserve aspect ratio, pad with YOLO gray
const scale = Math.min(640 / vw, 640 / vh);   // 0.6324
const newW  = Math.round(vw * scale);          // 379
const newH  = Math.round(vh * scale);          // 640
const padX  = Math.round((640 - newW) / 2);    // 131
const padY  = Math.round((640 - newH) / 2);    // 0

ctx.fillStyle = 'rgb(114,114,114)';            // YOLO standard gray
ctx.fillRect(0, 0, 640, 640);
ctx.drawImage(videoEl, padX, padY, newW, newH);
```

Result: `600×1012 → 379×640 + pad(131, 0)` centered in a 640×640 square.

**Before:** only `stack` detected at 50% confidence.
**After:** `stack(98%), check(97%), fold(91%), raise(88%), pot(80%),
7d(95%), Kd(87%)` — full card + button recognition.

### Coordinate Reversal

Detection coordinates from YOLO are in **640×640 letterbox space**.
They must be converted back to **normalized 0–1 image coordinates**
before the GameLoop can use them:

```
cx_orig = (cx_640 - padX) / (640 - 2 * padX)
cy_orig = (cy_640 - padY) / (640 - 2 * padY)
```

All downstream code (GameLoop, `_buildGameState`, hero region filter)
works with normalized coordinates in `[0, 1]`.

---

## 2. GameLoop State Machine

```
   ┌─────────┐  buttons + hero cards   ┌────────────┐
   │ WAITING  │ ──────────────────────→ │ PERCEPTION │
   │  5 FPS   │ ←───────────────────── │   30 FPS   │
   └─────────┘   timeout / no buttons  └─────┬──────┘
                                              │ stable × 3 frames
                                              ▼
                                       ┌─────────────┐
                  ┌─────────────────── │ CALCULATING  │
                  │  GTO + equity      │ (paused)     │
                  ▼                    └──────────────┘
           ┌────────────┐
           │ EXECUTING  │ → ADB tap
           │ (paused)   │
           └─────┬──────┘
                 │
                 ▼
           ┌────────────┐
           │ COOLDOWN   │ → wait for UI animation
           │  10 FPS    │ → confirm buttons gone
           └─────┬──────┘
                 │ buttons gone
                 ▼
           ┌─────────┐
           │ WAITING  │
           └─────────┘
```

### States

| State | FPS | Purpose |
|-------|-----|---------|
| **WAITING** | 5 | Low-power scan. Looks for action buttons **AND** hero cards. |
| **PERCEPTION** | 30 | High-speed accumulation. Waits for 3 consecutive stable frames. |
| **CALCULATING** | 0 (paused) | Runs SolverBridge + GTO equity. Vision paused to freeze detections. |
| **EXECUTING** | 0 (paused) | ADB tap on the chosen button bounding box. |
| **COOLDOWN** | 10 | Waits 1.5–5s for PPPoker chip animation, polls until buttons vanish. |

### Dynamic FPS Throttling

The key insight: running at 30 FPS constantly wastes GPU cycles and
generates heat.  The GameLoop dynamically adjusts vision FPS:

- `WAITING → PERCEPTION`: 5 → 30 FPS (ramp up when it's our turn)
- `PERCEPTION → CALCULATING`: 30 → 0 FPS (freeze frame for solver)
- `EXECUTING → COOLDOWN`: 0 → 10 FPS (poll for UI animation)
- `COOLDOWN → WAITING`: 10 → 5 FPS (drop back to idle scan)

FPS changes are sent via `vision:config` IPC and take effect on the
next capture cycle inside the inference BrowserWindow.

---

## 3. WAITING → PERCEPTION Gate (Hero Card Requirement)

### The Bug

The original `_handleWaiting()` only checked for action buttons:

```js
// ❌ BAD — transitions on buttons alone
if (buttons.length > 0) {
  this._transitionTo(LoopState.PERCEPTION);
}
```

This caused rapid **WAITING → PERCEPTION → timeout → WAITING** cycling
when the YOLO model detected button-like elements on an idle table
(e.g., menu buttons, false positives on UI chrome).

### The Logic

In PPPoker, if action buttons are visible (Fold / Check / Raise), it is
the hero's turn to act.  If it is the hero's turn, **hero cards MUST be
on screen**.  Therefore:

- **Buttons + hero cards** → genuine "our turn" → transition to PERCEPTION
- **Buttons + no hero cards** → false positive or idle table → stay in WAITING

### The Fix

```js
_handleWaiting(payload) {
  const buttons   = this._extractButtons(payload);
  if (buttons.length === 0) return;

  const heroCards = this._extractHeroCards(payload);
  if (heroCards.length === 0) {
    this._log.debug('Buttons but 0 hero cards — staying in WAITING');
    return;
  }

  // Both conditions met — enter PERCEPTION at 30 FPS
  this._transitionTo(LoopState.PERCEPTION);
  this._handlePerception(payload);
}
```

### Hero Region Definition

Hero cards sit in the **bottom ~35%** of the PPPoker screen.  Using
normalized coordinates (0 = top, 1 = bottom):

```
HERO_REGION_Y_NORM = 0.65
```

Any card detection with `cy > 0.65` is considered a hero card.
This constant is derived from the pixel threshold:
`700px / 1012px ≈ 0.69`, rounded down to `0.65` for safety margin.

---

## 4. Perception Stability Gate

Once in PERCEPTION (30 FPS), the GameLoop does **not** immediately act.
It waits for **3 consecutive frames** with an identical card signature
(sorted classId list).  This prevents acting on:

- Partially rendered boards (cards animating in)
- Flickering detections (confidence oscillation near threshold)
- Transient OCR ghosts

Only when `stableFrames >= 3` AND `cards.length >= 2` does the loop
freeze detections and advance to CALCULATING.

If no stability is reached within `PERCEPTION_TIMEOUT_MS = 2000ms`,
the loop falls back to WAITING at 5 FPS.

---

## 5. Detection Format

Each detection object flowing through the pipeline:

```ts
interface Detection {
  classId:    number;     // 0-51 = cards, 52-61 = buttons
  label:      string;     // e.g. "7d", "fold", "raise"
  confidence: number;     // 0.0 – 1.0
  cx:         number;     // center X, normalized [0, 1]
  cy:         number;     // center Y, normalized [0, 1]
  w:          number;     // width, normalized [0, 1]
  h:          number;     // height, normalized [0, 1]
}
```

### Class Ranges

| Range | Count | Description |
|-------|-------|-------------|
| 0–51 | 52 | Playing cards (4 suits × 13 ranks) |
| 52 | 1 | fold |
| 53 | 1 | check |
| 54 | 1 | raise |
| 55 | 1 | raise_2x |
| 56 | 1 | raise_2_5x |
| 57 | 1 | raise_pot |
| 58 | 1 | raise_confirm |
| 59 | 1 | allin |
| 60 | 1 | pot |
| 61 | 1 | stack |

---

## 6. Key Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `MODEL_INPUT_SIZE` | 640 | YOLO input square dimension |
| `DEFAULT_CONFIDENCE` | 0.35 | NMS confidence threshold |
| `DEFAULT_IOU` | 0.30 | NMS IoU threshold (low for overlapping PLO cards) |
| `MAX_DETECTIONS` | 24 | Cap: 6 hero + 5 board + buttons |
| `STABILITY_REQUIRED` | 3 | Consecutive stable frames for PERCEPTION → CALCULATING |
| `PERCEPTION_TIMEOUT_MS` | 2000 | Max time in PERCEPTION before falling back |
| `MIN_CARDS_FOR_ACTION` | 2 | Minimum card count to consider a frame valid |
| `HERO_REGION_Y_NORM` | 0.65 | Normalized Y threshold for hero card region |
| `COOLDOWN_FLOOR_MS` | 1500 | Minimum post-action wait (chip animation) |
| `COOLDOWN_CEILING_MS` | 5000 | Maximum post-action wait (stuck UI safety) |

---

## 7. GPU Stability

Electron's Windows Graphics Capture (WGC) can crash on some GPU drivers.
Mitigations applied:

- `--disable-features=WgcCapturerWin` — avoid buggy WGC codepath
- `--disable-gpu-sandbox` — prevent sandbox crashes on RTX drivers
- GPU process crash handler with automatic recovery
- WebGPU cache at `userData/GPUCache` (cleared on version mismatch)

---

## 8. Model Pipeline

```
titan_v7_hybrid.pt (PyTorch)
    │  ultralytics export format=onnx
    ▼
titan_v7_hybrid.onnx
    │  onnx-tf convert
    ▼
titan_v7_hybrid_saved_model/ (TF SavedModel)
    │  tensorflowjs_converter --input_format=tf_saved_model
    ▼
titan_v7_hybrid_tfjs/
    ├── model.json          (topology + weights manifest)
    ├── group1-shard1of3.bin
    ├── group1-shard2of3.bin
    └── group1-shard3of3.bin
```

Input shape: `[1, 640, 640, 3]` (NHWC, float32, normalized 0–1)
Output shape: `[1, 66, 8400]` (66 = 4 bbox + 62 class scores, 8400 anchor candidates)

Inference time: **25–35ms** on RTX 2060 Super (WebGPU backend).

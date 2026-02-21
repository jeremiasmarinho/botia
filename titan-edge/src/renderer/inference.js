/**
 * Inference Renderer — WebGPU YOLO Vision Engine (Hidden BrowserWindow)
 *
 * This script runs inside a hidden Electron BrowserWindow that serves
 * as a dedicated GPU inference engine. It uses TensorFlow.js with the
 * WebGPU backend to run YOLOv8 card detection at ~25-35ms on RTX 2060S.
 *
 * Architecture:
 *   ┌────────────────────────────────────────────────────────────────┐
 *   │  Hidden BrowserWindow (Chromium + WebGPU)                     │
 *   │                                                               │
 *   │  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────┐  │
 *   │  │ Capture  │ →  │ Preproc  │ →  │ TF.js    │ →  │  NMS   │  │
 *   │  │ Frame    │    │ 640×640  │    │ WebGPU   │    │ Filter │  │
 *   │  └──────────┘    └──────────┘    └──────────┘    └───┬────┘  │
 *   │                                                      │       │
 *   │                  ipcRenderer.send('vision:detections')│       │
 *   └──────────────────────────────────────────────────────┼───────┘
 *                                                          ↓
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │  Main Process (Node.js)                                      │
 *   │  → SolverBridge (Rust N-API) → GtoEngine → ADB execution    │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * IPC Protocol (renderer → main):
 *   'vision:detections'  → { detections: Detection[], inferenceMs, frameId, timestamp }
 *   'vision:status'      → { ready: bool, backend: string, modelClasses: number }
 *   'vision:error'       → { error: string, fatal: bool }
 *
 * IPC Protocol (main → renderer):
 *   'vision:start'       → { sourceId: string, fps?: number }
 *   'vision:stop'        → void
 *   'vision:config'      → { confidence?: number, iou?: number }
 */

"use strict";

// ── Constants ───────────────────────────────────────────────────────

const MODEL_INPUT_SIZE = 640;
const DEFAULT_CONFIDENCE = 0.35;
const DEFAULT_IOU = 0.3; // Low for overlapping PLO5/6 cards
const MAX_DETECTIONS = 24; // 6 hero + 5 board + buttons
const DEFAULT_FPS = 15; // Capture frame rate
const WARMUP_RUNS = 3; // Shader compilation warmup

// ── Class Names (62 classes: 52 cards + 10 buttons) ─────────────────

const CLASS_NAMES = {
  0: "2c",
  1: "2d",
  2: "2h",
  3: "2s",
  4: "3c",
  5: "3d",
  6: "3h",
  7: "3s",
  8: "4c",
  9: "4d",
  10: "4h",
  11: "4s",
  12: "5c",
  13: "5d",
  14: "5h",
  15: "5s",
  16: "6c",
  17: "6d",
  18: "6h",
  19: "6s",
  20: "7c",
  21: "7d",
  22: "7h",
  23: "7s",
  24: "8c",
  25: "8d",
  26: "8h",
  27: "8s",
  28: "9c",
  29: "9d",
  30: "9h",
  31: "9s",
  32: "Tc",
  33: "Td",
  34: "Th",
  35: "Ts",
  36: "Jc",
  37: "Jd",
  38: "Jh",
  39: "Js",
  40: "Qc",
  41: "Qd",
  42: "Qh",
  43: "Qs",
  44: "Kc",
  45: "Kd",
  46: "Kh",
  47: "Ks",
  48: "Ac",
  49: "Ad",
  50: "Ah",
  51: "As",
  52: "fold",
  53: "check",
  54: "raise",
  55: "raise_2x",
  56: "raise_2_5x",
  57: "raise_pot",
  58: "raise_confirm",
  59: "allin",
  60: "pot",
  61: "stack",
};

// ── State ───────────────────────────────────────────────────────────

let tf = null;
let model = null;
let backend = null;
let running = false;
let captureStream = null;
let videoEl = null;
let offscreenCanvas = null;
let offscreenCtx = null;
let frameId = 0;
let rafHandle = null;
let fpsInterval = 1000 / DEFAULT_FPS;
let lastFrameTime = 0;
let confidenceThreshold = DEFAULT_CONFIDENCE;
let iouThreshold = DEFAULT_IOU;

// ── Initialization ──────────────────────────────────────────────────

/**
 * Initialize TF.js with WebGPU backend and load the YOLO model.
 */
async function initModel() {
  try {
    tf = await import("@tensorflow/tfjs");
    await import("@tensorflow/tfjs-backend-webgpu");

    // Backend priority: WebGPU → WebGL → CPU (never acceptable)
    const backends = ["webgpu", "webgl"];
    let loaded = false;

    for (const b of backends) {
      try {
        await tf.setBackend(b);
        await tf.ready();
        backend = b;
        loaded = true;
        break;
      } catch {
        console.warn(`[Inference] Backend ${b} unavailable, trying next...`);
      }
    }

    if (!loaded) {
      sendError("No GPU backend available (WebGPU/WebGL both failed)", true);
      return;
    }

    console.log(`[Inference] TF.js backend: ${backend}`);

    // Load YOLO model from local assets
    // The model.json + weight shards should be in assets/yolo/
    const modelUrl = "../assets/yolo/model.json";
    model = await tf.loadGraphModel(modelUrl);

    // Verify model output shape
    console.log(
      "[Inference] Model loaded. Input shape:",
      model.inputs[0]?.shape,
    );

    // Warmup: first inference compiles GPU shaders (slow)
    const dummy = tf.zeros([1, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE, 3]);
    for (let i = 0; i < WARMUP_RUNS; i++) {
      const out = model.predict(dummy);
      if (Array.isArray(out)) out.forEach((t) => t.dispose());
      else out.dispose();
    }
    dummy.dispose();

    console.log(`[Inference] Warmup complete (${WARMUP_RUNS} runs)`);

    // Setup hidden video element for screen capture
    videoEl = document.createElement("video");
    videoEl.setAttribute("autoplay", "");
    videoEl.setAttribute("muted", "");

    // Offscreen canvas for frame extraction
    offscreenCanvas = new OffscreenCanvas(MODEL_INPUT_SIZE, MODEL_INPUT_SIZE);
    offscreenCtx = offscreenCanvas.getContext("2d", {
      willReadFrequently: false,
    });

    // Report ready status to main process
    sendStatus(true);
  } catch (err) {
    console.error("[Inference] Init failed:", err);
    sendError(`Init failed: ${err.message}`, true);
  }
}

// ── Screen Capture ──────────────────────────────────────────────────

/**
 * Start capturing from a desktop source (LDPlayer window).
 * @param {string} sourceId - Electron desktopCapturer source ID
 * @param {number} [fps=15] - Target capture FPS
 */
async function startCapture(sourceId, fps = DEFAULT_FPS) {
  if (running) stopCapture();

  fpsInterval = 1000 / fps;

  try {
    // Use Electron's desktopCapturer API via getUserMedia
    captureStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        mandatory: {
          chromeMediaSource: "desktop",
          chromeMediaSourceId: sourceId,
          minWidth: 640,
          maxWidth: 1920,
          minHeight: 480,
          maxHeight: 1080,
          maxFrameRate: fps,
        },
      },
    });

    videoEl.srcObject = captureStream;
    await videoEl.play();

    running = true;
    lastFrameTime = 0;
    requestInferenceLoop();

    console.log(`[Inference] Capture started: ${sourceId} @ ${fps}fps`);
  } catch (err) {
    console.error("[Inference] Capture failed:", err);
    sendError(`Capture failed: ${err.message}`, false);
  }
}

/** Stop capture and inference loop. */
function stopCapture() {
  running = false;

  if (rafHandle) {
    cancelAnimationFrame(rafHandle);
    rafHandle = null;
  }

  if (captureStream) {
    captureStream.getTracks().forEach((t) => t.stop());
    captureStream = null;
  }

  if (videoEl) {
    videoEl.srcObject = null;
  }

  console.log("[Inference] Capture stopped.");
}

// ── Inference Loop ──────────────────────────────────────────────────

/**
 * Main render loop — grab frame, infer, send detections.
 * Uses requestAnimationFrame throttled to target FPS.
 */
function requestInferenceLoop() {
  rafHandle = requestAnimationFrame(inferenceFrame);
}

async function inferenceFrame(timestamp) {
  if (!running) return;

  // Throttle to target FPS
  const elapsed = timestamp - lastFrameTime;
  if (elapsed < fpsInterval) {
    requestInferenceLoop();
    return;
  }
  lastFrameTime = timestamp;

  if (!model || !videoEl || videoEl.readyState < 2) {
    requestInferenceLoop();
    return;
  }

  const currentFrame = ++frameId;
  const t0 = performance.now();

  try {
    // 1. Grab frame → offscreen canvas → resize to 640×640
    offscreenCtx.drawImage(videoEl, 0, 0, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE);

    // 2. Preprocess: canvas → tensor → normalize [0,1] → batch dim
    const inputTensor = tf.tidy(() => {
      const pixels = tf.browser.fromPixels(offscreenCanvas);
      return pixels.div(255.0).expandDims(0);
    });

    // 3. Run YOLO inference on GPU
    const rawOutput = await model.predict(inputTensor);
    inputTensor.dispose();

    // 4. Post-process: decode boxes + class-aware NMS
    const detections = await postProcess(rawOutput);

    // 5. Dispose output tensors
    if (Array.isArray(rawOutput)) {
      rawOutput.forEach((t) => t.dispose());
    } else {
      rawOutput.dispose();
    }

    const inferenceMs = Math.round((performance.now() - t0) * 10) / 10;

    // 6. Send detections to main process via IPC
    sendDetections(detections, inferenceMs, currentFrame);
  } catch (err) {
    console.error("[Inference] Frame error:", err);
    // Don't kill the loop for transient errors
  }

  // Schedule next frame
  requestInferenceLoop();
}

// ── Post-Processing (NMS) ───────────────────────────────────────────

/**
 * Decode YOLOv8 output and apply Non-Maximum Suppression.
 *
 * YOLOv8 output: [1, 66, 8400] → transpose → [8400, 66]
 *   - [0..3] = cx, cy, w, h (box coordinates)
 *   - [4..65] = 62 class scores
 *
 * @param {tf.Tensor} rawOutput
 * @returns {Promise<Detection[]>}
 */
async function postProcess(rawOutput) {
  const output = Array.isArray(rawOutput) ? rawOutput[0] : rawOutput;

  // Squeeze batch dim + transpose: [66, 8400] → [8400, 66]
  const transposed = output.squeeze().transpose();
  const data = await transposed.array();
  transposed.dispose();

  const boxes = [];
  const scores = [];
  const classIds = [];

  for (let i = 0; i < data.length; i++) {
    const prediction = data[i];
    const cx = prediction[0];
    const cy = prediction[1];
    const w = prediction[2];
    const h = prediction[3];

    // Find best class score
    let maxScore = 0;
    let maxClass = 0;
    for (let c = 4; c < prediction.length; c++) {
      if (prediction[c] > maxScore) {
        maxScore = prediction[c];
        maxClass = c - 4;
      }
    }

    if (maxScore < confidenceThreshold) continue;

    // Convert to normalized [y1, x1, y2, x2] for TF.js NMS
    const x1 = (cx - w / 2) / MODEL_INPUT_SIZE;
    const y1 = (cy - h / 2) / MODEL_INPUT_SIZE;
    const x2 = (cx + w / 2) / MODEL_INPUT_SIZE;
    const y2 = (cy + h / 2) / MODEL_INPUT_SIZE;

    boxes.push([y1, x1, y2, x2]);
    scores.push(maxScore);
    classIds.push(maxClass);
  }

  if (boxes.length === 0) return [];

  // Apply class-aware NMS
  const boxesTensor = tf.tensor2d(boxes);
  const scoresTensor = tf.tensor1d(scores);

  const nmsIndices = await tf.image.nonMaxSuppressionAsync(
    boxesTensor,
    scoresTensor,
    MAX_DETECTIONS,
    iouThreshold,
    confidenceThreshold,
  );

  const selectedIndices = await nmsIndices.array();

  boxesTensor.dispose();
  scoresTensor.dispose();
  nmsIndices.dispose();

  // Build final detection array
  return selectedIndices.map((idx) => {
    const [y1, x1, y2, x2] = boxes[idx];
    return {
      classId: classIds[idx],
      label: CLASS_NAMES[classIds[idx]] || `class_${classIds[idx]}`,
      confidence: Math.round(scores[idx] * 1000) / 1000,
      cx: (x1 + x2) / 2,
      cy: (y1 + y2) / 2,
      w: x2 - x1,
      h: y2 - y1,
    };
  });
}

// ── Card Extraction Helpers ─────────────────────────────────────────

/**
 * Separate card detections from button detections.
 * Cards: classId 0-51, Buttons: classId 52-61.
 *
 * @param {Detection[]} detections
 * @returns {{ cards: string[], buttons: string[], raw: Detection[] }}
 */
function classifyDetections(detections) {
  const cards = [];
  const buttons = [];

  for (const d of detections) {
    if (d.classId <= 51) {
      cards.push(d.label);
    } else {
      buttons.push(d.label);
    }
  }

  return { cards, buttons, raw: detections };
}

// ── IPC Communication ───────────────────────────────────────────────

/**
 * Send detection results to main process.
 */
function sendDetections(detections, inferenceMs, fid) {
  const { cards, buttons, raw } = classifyDetections(detections);

  window.inferenceAPI.sendDetections({
    detections: raw,
    cards,
    buttons,
    inferenceMs,
    frameId: fid,
    timestamp: Date.now(),
    backend,
  });
}

function sendStatus(ready) {
  window.inferenceAPI.sendStatus({
    ready,
    backend,
    modelClasses: Object.keys(CLASS_NAMES).length,
    warmupRuns: WARMUP_RUNS,
  });
}

function sendError(error, fatal) {
  window.inferenceAPI.sendError({ error, fatal });
}

// ── IPC Listeners (main → renderer) ─────────────────────────────────

window.inferenceAPI.onStart(async (data) => {
  const { sourceId, fps } = data;
  console.log(
    `[Inference] Start command: source=${sourceId} fps=${fps || DEFAULT_FPS}`,
  );
  await startCapture(sourceId, fps || DEFAULT_FPS);
});

window.inferenceAPI.onStop(() => {
  console.log("[Inference] Stop command received.");
  stopCapture();
});

window.inferenceAPI.onConfig((config) => {
  if (config.confidence !== undefined) {
    confidenceThreshold = config.confidence;
    console.log(`[Inference] Confidence threshold → ${confidenceThreshold}`);
  }
  if (config.iou !== undefined) {
    iouThreshold = config.iou;
    console.log(`[Inference] IoU threshold → ${iouThreshold}`);
  }
});

// ── Bootstrap ───────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  console.log("[Inference] Hidden window loaded. Initializing model...");
  initModel();
});

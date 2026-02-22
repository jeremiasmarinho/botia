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

// tf is loaded globally via <script> tags in inference.html
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

// ADB frame queue for card detection (sent from main process)
let adbFrameQueue = [];

// Letterbox transform parameters (updated each frame)
let letterboxScale = 1;
let letterboxPadX = 0;
let letterboxPadY = 0;

// ── Initialization ──────────────────────────────────────────────────

/**
 * Initialize TF.js with WebGPU backend and load the YOLO model.
 */
async function initModel() {
  try {
    // tf is already loaded globally via <script> tags
    if (typeof tf === "undefined") {
      sendError("TF.js not loaded — check script tags in inference.html", true);
      return;
    }

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
    if (frameId % 25 === 0) {
      console.log(
        `[Inference] Waiting for video: readyState=${videoEl?.readyState || "null"}, model=${!!model}`,
      );
    }
    requestInferenceLoop();
    return;
  }

  const currentFrame = ++frameId;
  const t0 = performance.now();

  // Log every 50th frame for debugging
  if (currentFrame % 50 === 1) {
    console.log(
      `[Inference] Frame #${currentFrame} (video ${videoEl.videoWidth}×${videoEl.videoHeight})`,
    );
  }

  // Track tensors for guaranteed cleanup on any error path.
  // Without this, a throw between tf.tidy() and .dispose() leaks
  // one 640×640×3 float32 tensor (~4.7 MB) per failed frame.
  let inputTensor = null;
  let rawOutput = null;

  try {
    // 1. Grab frame → letterbox to 640×640 (preserve aspect ratio)
    const vw = videoEl.videoWidth;
    const vh = videoEl.videoHeight;
    if (vw > 0 && vh > 0) {
      letterboxScale = Math.min(MODEL_INPUT_SIZE / vw, MODEL_INPUT_SIZE / vh);
      const newW = Math.round(vw * letterboxScale);
      const newH = Math.round(vh * letterboxScale);
      letterboxPadX = Math.round((MODEL_INPUT_SIZE - newW) / 2);
      letterboxPadY = Math.round((MODEL_INPUT_SIZE - newH) / 2);

      // Fill with YOLO standard gray (114,114,114), then draw centered
      offscreenCtx.fillStyle = "rgb(114,114,114)";
      offscreenCtx.fillRect(0, 0, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE);
      offscreenCtx.drawImage(videoEl, letterboxPadX, letterboxPadY, newW, newH);
    } else {
      offscreenCtx.drawImage(videoEl, 0, 0, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE);
    }

    // Log letterbox params once
    if (currentFrame === 1) {
      console.log(
        `[Inference] Letterbox: ${vw}×${vh} → ${Math.round(vw * letterboxScale)}×${Math.round(vh * letterboxScale)} + pad(${letterboxPadX},${letterboxPadY}) in ${MODEL_INPUT_SIZE}×${MODEL_INPUT_SIZE}`,
      );
    }

    // DEBUG: save frame to disk every 60 frames (via context bridge to main process)
    if (currentFrame % 60 === 1) {
      try {
        const blob = await offscreenCanvas.convertToBlob({ type: "image/png" });
        const reader = new FileReader();
        reader.onloadend = () => {
          window.inferenceAPI.sendDebugFrame({
            frameId: currentFrame,
            dataUrl: reader.result,
          });
        };
        reader.readAsDataURL(blob);
      } catch (e) {
        console.warn("[Inference] Could not save debug frame:", e.message);
      }
    }

    // 2. Preprocess: canvas → tensor → normalize [0,1] → batch dim
    inputTensor = tf.tidy(() => {
      const pixels = tf.browser.fromPixels(offscreenCanvas);
      return pixels.div(255.0).expandDims(0);
    });

    // 3. Run YOLO inference on GPU
    rawOutput = await model.predict(inputTensor);

    // Debug: log output shape on first frame
    if (currentFrame <= 2) {
      const shape = Array.isArray(rawOutput)
        ? rawOutput.map((t) => t.shape)
        : rawOutput.shape;
      console.log(`[Inference] Output shape: ${JSON.stringify(shape)}`);
    }

    // 4. Post-process: decode boxes + class-aware NMS
    const detections = await postProcess(rawOutput);

    const inferenceMs = Math.round((performance.now() - t0) * 10) / 10;

    // Debug: log detection count periodically
    if (detections.length > 0 || currentFrame % 50 === 1) {
      console.log(
        `[Inference] Frame #${currentFrame}: ${detections.length} detections in ${inferenceMs}ms`,
      );
    }

    // 5. Send detections to main process via IPC
    sendDetections(detections, inferenceMs, currentFrame);
  } catch (err) {
    console.error("[Inference] Frame error:", err);
    // Don't kill the loop for transient errors
  } finally {
    // Guaranteed tensor cleanup — prevents memory leak in 12h+ sessions.
    if (inputTensor && !inputTensor.isDisposed) inputTensor.dispose();
    if (rawOutput) {
      const outputs = Array.isArray(rawOutput) ? rawOutput : [rawOutput];
      for (const t of outputs) {
        if (t && !t.isDisposed) t.dispose();
      }
    }
  }

  // ── Process pending ADB crop frames (for card detection) ──────
  while (adbFrameQueue.length > 0 && model) {
    const adbFrame = adbFrameQueue.shift();
    try {
      await processAdbCrop(adbFrame);
    } catch (e) {
      console.error("[Inference] ADB crop error:", e);
    }
  }

  // Schedule next frame
  requestInferenceLoop();
}

// ── ADB Crop Processing (higher-res card detection) ─────────────────

/**
 * Process a cropped ADB screenshot for card detection.
 * The main process sends a base64 PNG of a cropped region from
 * the 1080×1920 ADB screenshot. This gives much higher resolution
 * for cards than the desktopCapturer letterbox.
 *
 * @param {{ dataUrl: string, cropX: number, cropY: number, cropW: number, cropH: number, fullW: number, fullH: number, region: string }} data
 */
async function processAdbCrop(data) {
  const t0 = performance.now();

  // Load image from data URL
  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = data.dataUrl;
  });

  const vw = img.width;
  const vh = img.height;

  // Letterbox the crop into 640×640
  const cropLetterboxScale = Math.min(
    MODEL_INPUT_SIZE / vw,
    MODEL_INPUT_SIZE / vh,
  );
  const newW = Math.round(vw * cropLetterboxScale);
  const newH = Math.round(vh * cropLetterboxScale);
  const padX = Math.round((MODEL_INPUT_SIZE - newW) / 2);
  const padY = Math.round((MODEL_INPUT_SIZE - newH) / 2);

  offscreenCtx.fillStyle = "rgb(114,114,114)";
  offscreenCtx.fillRect(0, 0, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE);
  offscreenCtx.drawImage(img, padX, padY, newW, newH);

  console.log(
    `[Inference] ADB crop (${data.region}): ${vw}×${vh} → ${newW}×${newH} + pad(${padX},${padY})`,
  );

  let inputTensor = null;
  let rawOutput = null;

  try {
    inputTensor = tf.tidy(() => {
      return tf.browser.fromPixels(offscreenCanvas).div(255.0).expandDims(0);
    });

    rawOutput = await model.predict(inputTensor);

    // Pass crop dimensions directly to postProcess — no more global swap
    const detections = await postProcess(rawOutput, {
      srcW: vw,
      srcH: vh,
      scale: cropLetterboxScale,
      padX,
      padY,
    });

    const inferenceMs = Math.round((performance.now() - t0) * 10) / 10;

    // Map detections from crop-normalized to full-frame-normalized coords
    const mappedDetections = detections.map((d) => ({
      ...d,
      cx: (data.cropX + d.cx * data.cropW) / data.fullW,
      cy: (data.cropY + d.cy * data.cropH) / data.fullH,
      w: (d.w * data.cropW) / data.fullW,
      h: (d.h * data.cropH) / data.fullH,
      source: "adb-" + data.region,
    }));

    const cardCount = mappedDetections.filter((d) => d.classId <= 51).length;
    console.log(
      `[Inference] ADB ${data.region}: ${cardCount} cards, ${mappedDetections.length} total in ${inferenceMs}ms`,
    );

    // Always send result (even 0 cards) so main can clear stale detections.
    // Add a synthetic source-only detection when empty so region is identifiable.
    const toSend =
      mappedDetections.length > 0
        ? mappedDetections
        : [{ classId: -1, source: "adb-" + data.region, _empty: true }];
    sendDetections(toSend, inferenceMs, -1); // frameId -1 = ADB crop
  } finally {
    if (inputTensor && !inputTensor.isDisposed) inputTensor.dispose();
    if (rawOutput) {
      const outputs = Array.isArray(rawOutput) ? rawOutput : [rawOutput];
      for (const t of outputs) {
        if (t && !t.isDisposed) t.dispose();
      }
    }
  }
}

// ── Post-Processing (NMS) ───────────────────────────────────────────

/**
 * Decode YOLOv8 output and apply Non-Maximum Suppression.
 *
 * Original YOLOv8 (PyTorch/ONNX):  [1, 66, 8400] → need transpose → [8400, 66]
 * After onnx2tf (TFJS SavedModel): [1, 8400, 66]  → already correct → [8400, 66]
 *
 *   - [0..3] = cx, cy, w, h (box coordinates)
 *   - [4..65] = 62 class scores
 *
 * @param {tf.Tensor} rawOutput
 * @param {{ srcW?: number, srcH?: number, scale?: number, padX?: number, padY?: number }} [opts]
 * @returns {Promise<Detection[]>}
 */
async function postProcess(rawOutput, opts = {}) {
  const output = Array.isArray(rawOutput) ? rawOutput[0] : rawOutput;

  // Squeeze batch dim: [1, X, Y] → [X, Y]
  const squeezed = output.squeeze();
  const shape = squeezed.shape;

  // Auto-detect layout: if shape is [66, 8400] → transpose; if [8400, 66] → use as-is
  let data2d;
  if (shape[0] < shape[1]) {
    // shape is [66, 8400] (original NCHW order) → transpose to [8400, 66]
    const transposed = squeezed.transpose();
    data2d = await transposed.array();
    transposed.dispose();
  } else {
    // shape is [8400, 66] (onnx2tf NHWC order) → already correct
    data2d = await squeezed.array();
  }
  squeezed.dispose();

  const boxes = [];
  const scores = [];
  const classIds = [];

  // ── DEBUG: every 30 frames, dump ALL predictions above 0.10 ──
  const debugFrame = frameId % 30 === 1;
  const debugHits = [];
  // Extra debug: track best card-class predictions near hero area
  const debugCardHits = [];

  for (let i = 0; i < data2d.length; i++) {
    const prediction = data2d[i];
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

    // Debug: collect all predictions above 0.10
    if (debugFrame && maxScore >= 0.1) {
      debugHits.push({
        cls: CLASS_NAMES[maxClass] || `c${maxClass}`,
        score: Math.round(maxScore * 100),
        cx: Math.round(cx),
        cy: Math.round(cy),
        w: Math.round(w),
        h: Math.round(h),
      });
    }

    // Debug: find best card-class score for anchor boxes in hero region
    // Model coords: hero cards should be at ~y=400-550, x=200-500
    if (debugFrame && cy > 300 && cy < 600 && cx > 150 && cx < 500) {
      let bestCardScore = 0;
      let bestCardClass = 0;
      for (let c = 4; c < 56; c++) {
        // classes 0-51 are cards
        if (prediction[c] > bestCardScore) {
          bestCardScore = prediction[c];
          bestCardClass = c - 4;
        }
      }
      if (bestCardScore > 0.01) {
        debugCardHits.push({
          cls: CLASS_NAMES[bestCardClass] || `c${bestCardClass}`,
          score: Math.round(bestCardScore * 1000) / 10,
          allBest:
            CLASS_NAMES[maxClass] + "=" + Math.round(maxScore * 100) + "%",
          cx: Math.round(cx),
          cy: Math.round(cy),
        });
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

  // ── DEBUG: dump low-threshold hits ──
  if (debugFrame && debugHits.length > 0) {
    // Sort by score descending, show top 20
    debugHits.sort((a, b) => b.score - a.score);
    const top = debugHits.slice(0, 20);
    console.log(
      `[DEBUG] Frame #${frameId} — ${debugHits.length} predictions ≥10%:\n` +
        top
          .map((h) => `  ${h.cls}=${h.score}% @(${h.cx},${h.cy}) ${h.w}×${h.h}`)
          .join("\n"),
    );
  } else if (debugFrame) {
    console.log(`[DEBUG] Frame #${frameId} — 0 predictions ≥10%`);
  }

  // ── DEBUG: log card-class predictions in hero/board region ──
  if (debugFrame && debugCardHits.length > 0) {
    debugCardHits.sort((a, b) => b.score - a.score);
    const topCards = debugCardHits.slice(0, 10);
    console.log(
      `[DEBUG-CARDS] Frame #${frameId} — ${debugCardHits.length} card predictions ≥1% in hero region:\n` +
        topCards
          .map(
            (h) =>
              `  card:${h.cls}=${h.score}% (overall:${h.allBest}) @(${h.cx},${h.cy})`,
          )
          .join("\n"),
    );
  } else if (debugFrame) {
    console.log(
      `[DEBUG-CARDS] Frame #${frameId} — 0 card predictions ≥1% in hero region`,
    );
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

  // Build final detection array — reverse letterbox transform
  const origW = opts.srcW || (videoEl ? videoEl.videoWidth : MODEL_INPUT_SIZE);
  const origH = opts.srcH || (videoEl ? videoEl.videoHeight : MODEL_INPUT_SIZE);
  const lbScale = opts.scale != null ? opts.scale : letterboxScale;
  const lbPadX = opts.padX != null ? opts.padX : letterboxPadX;
  const lbPadY = opts.padY != null ? opts.padY : letterboxPadY;

  return selectedIndices.map((idx) => {
    const [y1, x1, y2, x2] = boxes[idx];
    // Convert normalized model coords → model pixels → remove pad → unscale → normalize to original image
    const ox1 = (x1 * MODEL_INPUT_SIZE - lbPadX) / lbScale / origW;
    const oy1 = (y1 * MODEL_INPUT_SIZE - lbPadY) / lbScale / origH;
    const ox2 = (x2 * MODEL_INPUT_SIZE - lbPadX) / lbScale / origW;
    const oy2 = (y2 * MODEL_INPUT_SIZE - lbPadY) / lbScale / origH;

    return {
      classId: classIds[idx],
      label: CLASS_NAMES[classIds[idx]] || `class_${classIds[idx]}`,
      confidence: Math.round(scores[idx] * 1000) / 1000,
      cx: (ox1 + ox2) / 2,
      cy: (oy1 + oy2) / 2,
      w: ox2 - ox1,
      h: oy2 - oy1,
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
    if (d.classId >= 0 && d.classId <= 51) {
      cards.push(d.label);
    } else if (d.classId >= 52) {
      buttons.push(d.label);
    }
    // classId < 0 = synthetic markers, skip
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
  if (config.fps !== undefined && config.fps > 0) {
    fpsInterval = 1000 / config.fps;
    console.log(
      `[Inference] FPS → ${config.fps} (interval=${fpsInterval.toFixed(1)}ms)`,
    );
  }
});

// ── ADB Frame Listener ─────────────────────────────────────────────
window.inferenceAPI.onAdbFrame((data) => {
  // Queue the ADB frame for processing in the next inference cycle
  // Keep max 4 in queue to prevent backup
  if (adbFrameQueue.length < 4) {
    adbFrameQueue.push(data);
  }
});

// ── Bootstrap ───────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  console.log("[Inference] Hidden window loaded. Initializing model...");
  initModel();
});

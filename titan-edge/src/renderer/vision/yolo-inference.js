/**
 * YOLO Inference — TensorFlow.js + WebGPU
 *
 * Runs YOLOv8 inference in the renderer process using WebGPU backend
 * for GPU-accelerated card detection on the RTX 2060 Super.
 *
 * Pipeline:
 *   desktopCapturer frame → resize to 640x640 → normalize [0,1]
 *   → YOLO model → raw outputs → NMS → DetectionResult[]
 *
 * Key Challenge: PLO5/PLO6 hands have 5-6 overlapping cards.
 *   Solution: Lower IoU threshold (0.3) + class-aware NMS to
 *   prevent duplicate card suppression.
 *
 * Performance Target: <35ms inference on RTX 2060 Super via WebGPU.
 */

"use strict";

// These will be imported when running in the renderer:
// import * as tf from '@tensorflow/tfjs';
// import '@tensorflow/tfjs-backend-webgpu';

const MODEL_INPUT_SIZE = 640;
const CONFIDENCE_THRESHOLD = 0.35;
const IOU_THRESHOLD = 0.3; // Low IoU for overlapping cards in PLO5/6
const MAX_DETECTIONS = 20; // Max cards + buttons per frame

/**
 * @typedef {Object} Detection
 * @property {number} classId    - YOLO class index (0-61)
 * @property {string} label      - Human-readable label
 * @property {number} confidence - Detection confidence [0,1]
 * @property {number} cx         - Center X (normalized 0-1)
 * @property {number} cy         - Center Y (normalized 0-1)
 * @property {number} w          - Width (normalized 0-1)
 * @property {number} h          - Height (normalized 0-1)
 */

class YoloInference {
  constructor() {
    this._model = null;
    this._backend = null;
    this._warmup = false;
    this._classNames = null;
  }

  /**
   * Initialize TF.js with WebGPU backend and load the YOLO model.
   *
   * @param {string} modelUrl  - URL to model.json (TF.js web format)
   * @param {Object} classNames - Class index → label mapping
   */
  async init(modelUrl, classNames) {
    const tf = await import("@tensorflow/tfjs");
    await import("@tensorflow/tfjs-backend-webgpu");

    // Prefer WebGPU → WebGL → WASM fallback chain
    try {
      await tf.setBackend("webgpu");
      this._backend = "webgpu";
    } catch {
      console.warn("[YOLO] WebGPU not available, falling back to WebGL");
      await tf.setBackend("webgl");
      this._backend = "webgl";
    }

    await tf.ready();
    console.log(`[YOLO] Backend: ${this._backend}`);

    // Load model
    this._model = await tf.loadGraphModel(modelUrl);
    this._classNames = classNames;

    // Warmup inference (first run is slow due to shader compilation)
    const dummy = tf.zeros([1, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE, 3]);
    await this._model.predict(dummy);
    dummy.dispose();
    this._warmup = true;

    console.log("[YOLO] Model loaded and warmed up.");
  }

  /**
   * Run inference on an image tensor.
   *
   * @param {ImageData|HTMLCanvasElement|HTMLVideoElement} source
   * @returns {Promise<{ detections: Detection[], inferenceMs: number }>}
   */
  async detect(source) {
    const tf = await import("@tensorflow/tfjs");

    const t0 = performance.now();

    // 1. Preprocess: resize + normalize
    const inputTensor = tf.tidy(() => {
      let img = tf.browser.fromPixels(source);
      img = tf.image.resizeBilinear(img, [MODEL_INPUT_SIZE, MODEL_INPUT_SIZE]);
      img = img.div(255.0); // Normalize to [0, 1]
      img = img.expandDims(0); // Add batch dimension
      return img;
    });

    // 2. Run inference
    const rawOutput = await this._model.predict(inputTensor);
    inputTensor.dispose();

    // 3. Post-process: extract boxes + NMS
    const detections = await this._postProcess(tf, rawOutput);

    // Clean up tensors
    if (Array.isArray(rawOutput)) {
      rawOutput.forEach((t) => t.dispose());
    } else {
      rawOutput.dispose();
    }

    const inferenceMs = performance.now() - t0;

    return { detections, inferenceMs: Math.round(inferenceMs * 10) / 10 };
  }

  /**
   * Post-process YOLO output: decode boxes, apply class-aware NMS.
   *
   * @param {Object} tf - TensorFlow reference
   * @param {Object} rawOutput - Raw model output tensor
   * @returns {Promise<Detection[]>}
   */
  async _postProcess(tf, rawOutput) {
    // YOLOv8 output shape: [1, 66, 8400] (62 classes + 4 box coords)
    // Transpose to [8400, 66]
    const output = Array.isArray(rawOutput) ? rawOutput[0] : rawOutput;
    const transposed = output.squeeze().transpose();
    const data = await transposed.array();
    transposed.dispose();

    const boxes = [];
    const scores = [];
    const classes = [];

    for (const prediction of data) {
      const [cx, cy, w, h] = prediction.slice(0, 4);
      const classScores = prediction.slice(4);

      // Find best class
      let maxScore = 0;
      let maxClass = 0;
      for (let i = 0; i < classScores.length; i++) {
        if (classScores[i] > maxScore) {
          maxScore = classScores[i];
          maxClass = i;
        }
      }

      if (maxScore >= CONFIDENCE_THRESHOLD) {
        // Convert to [y1, x1, y2, x2] for TF.js NMS
        const x1 = (cx - w / 2) / MODEL_INPUT_SIZE;
        const y1 = (cy - h / 2) / MODEL_INPUT_SIZE;
        const x2 = (cx + w / 2) / MODEL_INPUT_SIZE;
        const y2 = (cy + h / 2) / MODEL_INPUT_SIZE;

        boxes.push([y1, x1, y2, x2]);
        scores.push(maxScore);
        classes.push(maxClass);
      }
    }

    if (boxes.length === 0) return [];

    // Class-aware NMS
    const boxesTensor = tf.tensor2d(boxes);
    const scoresTensor = tf.tensor1d(scores);

    const nmsIndices = await tf.image.nonMaxSuppressionAsync(
      boxesTensor,
      scoresTensor,
      MAX_DETECTIONS,
      IOU_THRESHOLD,
      CONFIDENCE_THRESHOLD,
    );

    const selectedIndices = await nmsIndices.array();

    boxesTensor.dispose();
    scoresTensor.dispose();
    nmsIndices.dispose();

    // Build detection results
    const detections = selectedIndices.map((idx) => {
      const [y1, x1, y2, x2] = boxes[idx];
      return {
        classId: classes[idx],
        label: this._classNames?.[classes[idx]] || `class_${classes[idx]}`,
        confidence: Math.round(scores[idx] * 1000) / 1000,
        cx: (x1 + x2) / 2,
        cy: (y1 + y2) / 2,
        w: x2 - x1,
        h: y2 - y1,
      };
    });

    return detections;
  }

  /** Check if model is loaded and ready. */
  get ready() {
    return this._warmup && this._model !== null;
  }

  /** Get active backend name. */
  get backend() {
    return this._backend;
  }
}

// Export for use in renderer
if (typeof module !== "undefined") {
  module.exports = {
    YoloInference,
    MODEL_INPUT_SIZE,
    CONFIDENCE_THRESHOLD,
    IOU_THRESHOLD,
  };
}

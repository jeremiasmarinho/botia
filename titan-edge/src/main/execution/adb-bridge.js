/**
 * ADB Bridge — Ghost Tap Execution Layer (v3: Zero-Queue Architecture)
 *
 * Connects to LDPlayer via Android Debug Bridge and executes invisible
 * taps directly on the Android kernel, bypassing Windows mouse detection.
 *
 * Three-Layer Anti-Detection Defense:
 *
 *   1. GAUSSIAN TAP DISTRIBUTION
 *      humanizedTap(bbox) receives a YOLO bounding box and samples the
 *      touch point from a 2D Gaussian centered on the box.  σ is set so
 *      that 95% of taps land inside the button (2σ = half-width), but
 *      no two taps ever hit the exact same pixel.
 *
 *   2. COGNITIVE DELAY
 *      Before every action, a delay drawn from the Humanizer's Poisson
 *      distribution (400-1200ms easy, up to 4500ms hard) simulates the
 *      time a human takes to read the board, count outs, and decide.
 *
 *   3. ACTION MUTEX (Drop-If-Locked)
 *      An async lock prevents overlapping ADB commands.  When one action
 *      is in-flight (delay → tap → cooldown), subsequent calls to
 *      executeAction() are IMMEDIATELY DROPPED — not queued.  This
 *      prevents stale-frame execution: a decision computed 800ms ago
 *      against a board state that may have changed is NEVER executed.
 *      The Game Loop will re-perceive and re-decide from a fresh frame.
 *
 *      CRITICAL: Queuing was REMOVED in v3 because queued actions carry
 *      stale board context.  During the 800ms cognitive delay + cooldown
 *      of action A, the board state can change (villain raises, new
 *      street dealt).  Any queued action B was computed against the OLD
 *      board and would be incorrect.  Drop-if-locked forces the Game
 *      Loop to re-perceive → re-compute → re-execute from scratch.
 *
 * Architecture:
 *   GameLoop → executeAction(bbox, difficulty)
 *            → if locked: return {dropped: true} immediately
 *            → if free:   lock → delay → tap → cooldown → unlock
 *   Node.js child_process.execFile → adb.exe → LDPlayer kernel
 *
 * Why ADB over pyautogui/Win32:
 *   1. Taps go to the Android input subsystem, not the Windows desktop.
 *   2. No mouse cursor movement — invisible to anti-bot screenshot checks.
 *   3. Deterministic delivery — no pixel-to-window offset calculation.
 *   4. Works even if emulator window is minimized or occluded.
 *
 * Usage:
 *   const bridge = new AdbBridge();
 *   await bridge.connect();
 *
 *   // Raw tap (dev/debug only — no protection):
 *   await bridge.tap(540, 960);
 *
 *   // Humanized tap on a YOLO bounding box:
 *   await bridge.humanizedTap({ x: 520, y: 940, width: 80, height: 36 });
 *
 *   // Full action pipeline (cognitive delay + mutex + humanized tap):
 *   const result = await bridge.executeAction(bbox, 'medium');
 *   if (result.dropped) { // action was in-flight, will re-perceive }
 */

"use strict";

const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const path = require("node:path");
const { EventEmitter } = require("node:events");

const { Humanizer, Difficulty } = require("./humanizer");

const execFileAsync = promisify(execFile);

// ── Defaults ────────────────────────────────────────────────────────

/** Default ADB paths for common LDPlayer installations */
const DEFAULT_ADB_PATHS = [
  "F:\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\LDPlayer\\LDPlayer4.0\\adb.exe",
  "C:\\Program Files\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\Program Files (x86)\\LDPlayer\\LDPlayer9\\adb.exe",
];

/** Default LDPlayer ADB device address */
const DEFAULT_DEVICE = "emulator-5554";

/** Timeout for ADB commands (ms) */
const ADB_TIMEOUT = 5_000;

// ── Gaussian Tap Constants ──────────────────────────────────────────

/**
 * The Gaussian σ is set so that 2σ = half the bounding box dimension.
 * This means 95.45% of samples fall within the box (±2σ coverage).
 * The divisor controls the σ-to-halfwidth ratio:
 *   SIGMA_DIVISOR = 4  →  σ = width / 4  →  95.45% inside
 *   SIGMA_DIVISOR = 3  →  σ = width / 3  →  86.6% inside (riskier)
 *   SIGMA_DIVISOR = 6  →  σ = width / 6  →  99.7% inside (too tight)
 */
const SIGMA_DIVISOR = 4;

/**
 * Minimum σ in pixels — prevents degenerate distributions on very
 * small YOLO boxes where σ → 0 would produce perfectly centered taps.
 */
const MIN_SIGMA_PX = 2;

/**
 * Maximum retry attempts when Gaussian sample falls outside the
 * bounding box.  After MAX_RESAMPLE attempts, we clamp to box edge
 * with a small inward margin.
 */
const MAX_RESAMPLE = 5;

/** Inward margin (px) when clamping an out-of-bounds sample */
const CLAMP_MARGIN_PX = 3;

// ── Action Mutex Constants ──────────────────────────────────────────

/**
 * Post-action cooldown (ms) — how long to lock after an ADB tap
 * completes, to allow the emulator's UI animation to finish.
 *
 * PPPoker button animations take ~400-600ms.  We add a safety
 * margin of 200ms → 800ms total minimum between actions.
 */
const POST_ACTION_COOLDOWN_MS = 800;

// NOTE: Queue was REMOVED in v3. See file header for rationale.
// All calls during action lock are dropped immediately.

/**
 * @typedef {Object} BoundingBox
 * @property {number} x      - Left edge X (Android pixel space)
 * @property {number} y      - Top edge Y (Android pixel space)
 * @property {number} width   - Box width in pixels
 * @property {number} height  - Box height in pixels
 */

/**
 * @typedef {Object} AdbBridgeOptions
 * @property {string}  [adbPath]     - Absolute path to adb.exe
 * @property {string}  [device]      - ADB device serial (e.g. 'emulator-5554')
 * @property {number}  [timeout]     - Command timeout in ms
 * @property {boolean} [dryRun]      - If true, logs commands without executing
 * @property {number}  [cooldownMs]  - Post-action cooldown override
 */

class AdbBridge extends EventEmitter {
  /** @param {AdbBridgeOptions} opts */
  constructor(opts = {}) {
    super();
    this._adbPath = opts.adbPath || null;
    this._device = opts.device || DEFAULT_DEVICE;
    this._timeout = opts.timeout || ADB_TIMEOUT;
    this._dryRun = opts.dryRun || false;
    this._cooldownMs = opts.cooldownMs || POST_ACTION_COOLDOWN_MS;

    this._connected = false;
    this._deviceModel = null;
    this._screenSize = { width: 0, height: 0 };

    // ── Action Mutex State (v3: Zero-Queue) ─────────────────────
    /** @type {boolean} True while an action is being processed */
    this._actionLocked = false;
    /** @type {{ total: number, dropped: number, avgDelayMs: number }} */
    this._stats = { total: 0, dropped: 0, avgDelayMs: 0 };
  }

  // ── Connection ──────────────────────────────────────────────────

  /**
   * Locate adb.exe, verify device connectivity, and cache device info.
   * @returns {Promise<{ device: string, model: string, screen: { width: number, height: number } }>}
   */
  async connect() {
    // 1. Resolve ADB binary
    this._adbPath = this._adbPath || (await this._findAdb());
    if (!this._adbPath) {
      throw new Error(
        "[AdbBridge] adb.exe not found. Provide adbPath or install LDPlayer.",
      );
    }

    // 1b. Force TCP connect (LDPlayer local bridge)
    for (const port of [5555, 5557]) {
      try {
        const res = await this._exec(["connect", `127.0.0.1:${port}`]);
        const out = res.stdout.trim();
        if (out.includes("connected") || out.includes("already")) break;
      } catch {
        // Ignore — will try next port
      }
    }

    // 2. Verify device is online
    const devices = await this._exec(["devices"]);
    const lines = devices.stdout.trim().split("\n").slice(1); // skip header
    const online = lines
      .map((l) => l.trim().split(/\s+/))
      .filter((parts) => parts[1] === "device")
      .map((parts) => parts[0]);

    if (!online.includes(this._device)) {
      // Try to find any available device
      if (online.length > 0) {
        this._device = online[0];
        this.emit("warn", `Device auto-selected: ${this._device}`);
      } else {
        throw new Error(
          `[AdbBridge] No online ADB devices. Start LDPlayer first.\n` +
            `  Searched for: ${this._device}\n` +
            `  Online: ${online.join(", ") || "(none)"}`,
        );
      }
    }

    // 3. Get device model
    try {
      const model = await this._shell(["getprop", "ro.product.model"]);
      this._deviceModel = model.stdout.trim() || "Unknown";
    } catch {
      this._deviceModel = "Unknown";
    }

    // 4. Get screen resolution
    try {
      const wm = await this._shell(["wm", "size"]);
      const match = wm.stdout.match(/(\d+)x(\d+)/);
      if (match) {
        this._screenSize = {
          width: parseInt(match[1], 10),
          height: parseInt(match[2], 10),
        };
      }
    } catch {
      this._screenSize = { width: 0, height: 0 };
    }

    this._connected = true;

    const info = {
      device: this._device,
      model: this._deviceModel,
      screen: { ...this._screenSize },
    };

    this.emit("connected", info);
    return info;
  }

  /** Check if bridge is connected and device is responsive. */
  async ping() {
    try {
      const result = await this._shell(["echo", "pong"]);
      return result.stdout.trim() === "pong";
    } catch {
      return false;
    }
  }

  // ── Raw Tap ─────────────────────────────────────────────────────

  /**
   * Send a raw tap at exact coordinates.
   *
   * @param {number} x - X coordinate (Android pixel space)
   * @param {number} y - Y coordinate (Android pixel space)
   * @returns {Promise<{ x: number, y: number, durationMs: number }>}
   */
  async tap(x, y) {
    this._assertConnected();

    const ix = Math.round(x);
    const iy = Math.round(y);

    const t0 = performance.now();
    await this._shell(["input", "tap", String(ix), String(iy)]);
    const durationMs = performance.now() - t0;

    const result = { x: ix, y: iy, durationMs: Math.round(durationMs) };
    this.emit("tap", result);
    return result;
  }

  // ── Humanized Tap (Gaussian BBox Distribution) ─────────────────

  /**
   * Execute a humanized tap inside a YOLO bounding box using a 2D
   * Gaussian distribution.
   *
   * The touch point is sampled from N(center, σ²) where:
   *   σ_x = max(bbox.width  / SIGMA_DIVISOR, MIN_SIGMA_PX)
   *   σ_y = max(bbox.height / SIGMA_DIVISOR, MIN_SIGMA_PX)
   *
   * This guarantees:
   *   - 95.45% of taps land INSIDE the bounding box (±2σ)
   *   - No two taps hit the same pixel (continuous distribution)
   *   - Small buttons still get meaningful spread (MIN_SIGMA_PX)
   *   - Out-of-bounds samples are resampled up to MAX_RESAMPLE
   *     times, then clamped with CLAMP_MARGIN_PX inward margin
   *
   * @param {BoundingBox} bbox - YOLO detection { x, y, width, height }
   *                             where (x,y) is the TOP-LEFT corner
   * @returns {Promise<{
   *   tapX: number, tapY: number,
   *   centerX: number, centerY: number,
   *   offsetX: number, offsetY: number,
   *   sigmaX: number, sigmaY: number,
   *   resamples: number,
   *   durationMs: number
   * }>}
   */
  async humanizedTap(bbox) {
    this._assertConnected();

    const centerX = bbox.x + bbox.width / 2;
    const centerY = bbox.y + bbox.height / 2;
    const sigmaX = Math.max(bbox.width / SIGMA_DIVISOR, MIN_SIGMA_PX);
    const sigmaY = Math.max(bbox.height / SIGMA_DIVISOR, MIN_SIGMA_PX);

    // Sample from 2D Gaussian, resample if outside box
    let tapX, tapY;
    let resamples = 0;

    for (let attempt = 0; attempt <= MAX_RESAMPLE; attempt++) {
      tapX = centerX + gaussianRandom() * sigmaX;
      tapY = centerY + gaussianRandom() * sigmaY;

      // Check if inside bounding box
      if (
        tapX >= bbox.x &&
        tapX <= bbox.x + bbox.width &&
        tapY >= bbox.y &&
        tapY <= bbox.y + bbox.height
      ) {
        break; // Valid sample
      }

      resamples++;

      // Last attempt: clamp to box with inward margin
      if (attempt === MAX_RESAMPLE) {
        tapX = clamp(
          tapX,
          bbox.x + CLAMP_MARGIN_PX,
          bbox.x + bbox.width - CLAMP_MARGIN_PX,
        );
        tapY = clamp(
          tapY,
          bbox.y + CLAMP_MARGIN_PX,
          bbox.y + bbox.height - CLAMP_MARGIN_PX,
        );
      }
    }

    // Execute the physical tap
    const tapResult = await this.tap(tapX, tapY);

    const result = {
      tapX: tapResult.x,
      tapY: tapResult.y,
      centerX: Math.round(centerX),
      centerY: Math.round(centerY),
      offsetX: Math.round((tapX - centerX) * 10) / 10,
      offsetY: Math.round((tapY - centerY) * 10) / 10,
      sigmaX: Math.round(sigmaX * 10) / 10,
      sigmaY: Math.round(sigmaY * 10) / 10,
      resamples,
      durationMs: tapResult.durationMs,
    };

    this.emit("humanizedTap", result);
    return result;
  }

  // ── Full Action Pipeline (Cognitive Delay + Mutex + Tap) ──────

  /**
   * Execute a complete human-simulated action:
   *
   *   1. MUTEX CHECK: If locked → DROP immediately (zero-queue)
   *   2. LOCK ACQUIRE: Set _actionLocked = true
   *   3. COGNITIVE DELAY: Humanizer.reactionDelay(difficulty) — 400ms to 4.5s
   *   4. HUMANIZED TAP: Gaussian sample inside bbox
   *   5. POST-ACTION COOLDOWN: Wait for UI animation (800ms)
   *   6. LOCK RELEASE: Return to WAITING state
   *
   * ZERO-QUEUE POLICY (v3): If an action is already in-flight, the
   * incoming request is DROPPED.  The caller (Game Loop) will
   * re-perceive → re-decide from a fresh frame.  This eliminates
   * the stale-frame vulnerability where a queued action from 800ms
   * ago executes against a board that has changed.
   *
   * This is the ONLY method that should be called from the Game Loop.
   * Raw tap() and humanizedTap() bypass all protections.
   *
   * @param {BoundingBox} bbox - YOLO detection of the target button
   * @param {string} [difficulty='medium'] - 'easy' | 'medium' | 'hard'
   * @returns {Promise<{
   *   tapX: number, tapY: number,
   *   cognitiveDelayMs: number,
   *   cooldownMs: number,
   *   totalMs: number,
   *   dropped?: boolean
   * }>}
   */
  async executeAction(bbox, difficulty = Difficulty.MEDIUM) {
    // v3 Zero-Queue: DROP immediately if locked
    if (this._actionLocked) {
      this._stats.dropped++;
      this.emit("actionDropped", {
        reason: "action_in_flight",
        bbox,
      });
      return { dropped: true, reason: "action_in_flight" };
    }

    // Acquire lock and execute
    return this._executeLockedAction(bbox, difficulty);
  }

  /**
   * Internal: Execute a single action while holding the mutex.
   * @private
   */
  async _executeLockedAction(bbox, difficulty) {
    this._actionLocked = true;
    this._stats.total++;

    const t0 = performance.now();

    try {
      // ── Phase 1: Cognitive Delay ──────────────────────────────
      // Simulates the human reading the board, counting outs,
      // and making a decision before physically moving.
      const cognitiveDelayMs = Humanizer.reactionDelay(difficulty);
      await sleep(cognitiveDelayMs);

      // ── Phase 2: Humanized Tap ────────────────────────────────
      const tapResult = await this.humanizedTap(bbox);

      // ── Phase 3: Post-Action Cooldown ─────────────────────────
      // Wait for the PPPoker UI animation to complete before
      // allowing the next action.  Without this, the same button
      // detected in the next frame would trigger a duplicate tap.
      const cooldownMs = this._cooldownMs + randomInt(0, 200);
      await sleep(cooldownMs);

      const totalMs = Math.round(performance.now() - t0);

      // Update running average delay
      const n = this._stats.total;
      this._stats.avgDelayMs = Math.round(
        (this._stats.avgDelayMs * (n - 1) + totalMs) / n,
      );

      const result = {
        tapX: tapResult.tapX,
        tapY: tapResult.tapY,
        centerX: tapResult.centerX,
        centerY: tapResult.centerY,
        offsetX: tapResult.offsetX,
        offsetY: tapResult.offsetY,
        cognitiveDelayMs,
        cooldownMs,
        totalMs,
        difficulty,
      };

      this.emit("actionComplete", result);
      return result;
    } catch (err) {
      this.emit("actionError", { error: err.message, bbox, difficulty });
      throw err;
    } finally {
      // v3: Simply release lock — no queue to drain
      this._actionLocked = false;
    }
  }

  // ── Legacy Ghost Tap (kept for backward compat / tests) ───────

  /**
   * Simple humanized tap with Gaussian jitter at a raw coordinate.
   * Does NOT use the mutex or cognitive delay.
   * Prefer executeAction() for production use.
   *
   * @param {number} x - Target X coordinate
   * @param {number} y - Target Y coordinate
   * @param {Object}  [opts]
   * @param {number}  [opts.jitter=3] - Pixel jitter σ
   * @returns {Promise<{ x: number, y: number, jitterX: number, jitterY: number, durationMs: number }>}
   */
  async ghostTap(x, y, opts = {}) {
    const jitter = opts.jitter ?? 3;

    const jitterX = gaussianRandom() * jitter;
    const jitterY = gaussianRandom() * jitter;
    const finalX = x + jitterX;
    const finalY = y + jitterY;

    const tapResult = await this.tap(finalX, finalY);

    const result = {
      x: tapResult.x,
      y: tapResult.y,
      jitterX: Math.round(jitterX * 10) / 10,
      jitterY: Math.round(jitterY * 10) / 10,
      durationMs: tapResult.durationMs,
    };

    this.emit("ghostTap", result);
    return result;
  }

  // ── Swipe (for slider actions like raise amount) ──────────────

  /**
   * Execute a swipe gesture (e.g., drag raise slider).
   *
   * @param {number} x1 - Start X
   * @param {number} y1 - Start Y
   * @param {number} x2 - End X
   * @param {number} y2 - End Y
   * @param {number} [durationMs=300] - Swipe duration
   */
  async swipe(x1, y1, x2, y2, durationMs = 300) {
    this._assertConnected();
    await this._shell([
      "input",
      "swipe",
      String(Math.round(x1)),
      String(Math.round(y1)),
      String(Math.round(x2)),
      String(Math.round(y2)),
      String(Math.round(durationMs)),
    ]);
  }

  // ── Long Press ────────────────────────────────────────────────

  /**
   * Execute a long press (swipe to same point with duration).
   *
   * @param {number} x
   * @param {number} y
   * @param {number} [holdMs=500]
   */
  async longPress(x, y, holdMs = 500) {
    await this.swipe(x, y, x, y, holdMs);
  }

  // ── Screenshot ────────────────────────────────────────────────

  /**
   * Capture a screenshot from the emulator via ADB.
   * Returns the raw PNG buffer.
   *
   * @returns {Promise<Buffer>}
   */
  async screenshot() {
    this._assertConnected();
    const result = await this._exec(
      ["-s", this._device, "exec-out", "screencap", "-p"],
      { encoding: "buffer", maxBuffer: 10 * 1024 * 1024 },
    );
    return result.stdout;
  }

  // ── Internals ─────────────────────────────────────────────────

  _assertConnected() {
    if (!this._connected) {
      throw new Error("[AdbBridge] Not connected. Call connect() first.");
    }
  }

  /**
   * Execute an ADB command.
   * @param {string[]} args
   * @param {Object}   [opts]
   */
  async _exec(args, opts = {}) {
    // In dryRun mode, still allow read-only / connectivity commands
    // (connect, devices, shell getprop, shell wm size, shell settings get)
    // Only block mutating commands (shell input, shell am, install, push, etc.)
    const isReadOnly =
      args[0] === "connect" ||
      args[0] === "devices" ||
      args[0] === "version" ||
      (args.includes("shell") &&
        (args.includes("getprop") ||
          args.includes("wm") ||
          args.includes("settings")));

    if (this._dryRun && !isReadOnly) {
      const cmd = `${this._adbPath} ${args.join(" ")}`;
      this.emit("dryRun", cmd);
      return { stdout: "", stderr: "" };
    }

    return execFileAsync(this._adbPath, args, {
      timeout: this._timeout,
      windowsHide: true,
      ...opts,
    });
  }

  /**
   * Execute a shell command on the connected device.
   * @param {string[]} shellArgs
   */
  async _shell(shellArgs) {
    return this._exec(["-s", this._device, "shell", ...shellArgs]);
  }

  /**
   * Auto-detect adb.exe from known LDPlayer installation paths.
   * @returns {Promise<string|null>}
   */
  async _findAdb() {
    const fs = require("node:fs");

    for (const candidate of DEFAULT_ADB_PATHS) {
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    }

    // Fallback: try system PATH
    try {
      const { execFileSync } = require("node:child_process");
      execFileSync("adb", ["version"], { timeout: 3000, windowsHide: true });
      return "adb"; // available in PATH
    } catch {
      return null;
    }
  }

  // ── Getters ───────────────────────────────────────────────────

  get connected() {
    return this._connected;
  }
  get device() {
    return this._device;
  }
  get deviceModel() {
    return this._deviceModel;
  }
  get screenSize() {
    return { ...this._screenSize };
  }
  get actionLocked() {
    return this._actionLocked;
  }
  get stats() {
    return { ...this._stats };
  }
}

// ── Utility Functions ───────────────────────────────────────────────

/** Box-Muller transform — returns a single standard normal variate. */
function gaussianRandom() {
  const u1 = Math.random();
  const u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1 || 1e-10)) * Math.cos(2 * Math.PI * u2);
}

/** Uniformly random integer in [min, max]. */
function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

/** Clamp value between lo and hi. */
function clamp(value, lo, hi) {
  return Math.max(lo, Math.min(hi, value));
}

/** Promise-based sleep. */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = { AdbBridge, DEFAULT_ADB_PATHS, DEFAULT_DEVICE };

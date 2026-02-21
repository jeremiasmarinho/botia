/**
 * ADB Bridge — Ghost Tap Execution Layer
 *
 * Connects to LDPlayer via Android Debug Bridge and executes invisible
 * taps directly on the Android kernel, bypassing Windows mouse detection.
 *
 * Architecture:
 *   Node.js child_process.execFile → adb.exe → LDPlayer kernel
 *
 * Why ADB over pyautogui/Win32:
 *   1. Taps go to the Android input subsystem, not the Windows desktop.
 *   2. No mouse cursor movement — invisible to anti-bot screenshot checks.
 *   3. Deterministic delivery — no pixel-to-window offset calculation.
 *   4. Works even if emulator window is minimized or occluded.
 *
 * Usage:
 *   const bridge = new AdbBridge({ adbPath: 'C:/path/to/adb.exe' });
 *   await bridge.connect();
 *   await bridge.tap(540, 960);          // Simple tap
 *   await bridge.ghostTap(540, 960);     // Humanized tap with jitter
 */

"use strict";

const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const path = require("node:path");
const { EventEmitter } = require("node:events");

const execFileAsync = promisify(execFile);

// ── Defaults ────────────────────────────────────────────────────────

/** Default ADB paths for common LDPlayer installations */
const DEFAULT_ADB_PATHS = [
  "C:\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\LDPlayer\\LDPlayer4.0\\adb.exe",
  "C:\\Program Files\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\Program Files (x86)\\LDPlayer\\LDPlayer9\\adb.exe",
];

/** Default LDPlayer ADB device address */
const DEFAULT_DEVICE = "emulator-5554";

/** Timeout for ADB commands (ms) */
const ADB_TIMEOUT = 5_000;

// ── Humanizer Constants ─────────────────────────────────────────────

/** Gaussian jitter standard deviation in pixels */
const TAP_JITTER_PX = 3;

/** Base delay range between taps [min, max] in ms */
const TAP_DELAY_RANGE = [80, 250];

/** Probability of a micro-pause (simulates hesitation) */
const MICRO_PAUSE_PROB = 0.12;

/** Micro-pause duration range [min, max] in ms */
const MICRO_PAUSE_RANGE = [300, 800];

/**
 * @typedef {Object} AdbBridgeOptions
 * @property {string}  [adbPath]     - Absolute path to adb.exe
 * @property {string}  [device]      - ADB device serial (e.g. 'emulator-5554')
 * @property {number}  [timeout]     - Command timeout in ms
 * @property {boolean} [dryRun]      - If true, logs commands without executing
 */

class AdbBridge extends EventEmitter {
  /** @param {AdbBridgeOptions} opts */
  constructor(opts = {}) {
    super();
    this._adbPath = opts.adbPath || null;
    this._device = opts.device || DEFAULT_DEVICE;
    this._timeout = opts.timeout || ADB_TIMEOUT;
    this._dryRun = opts.dryRun || false;

    this._connected = false;
    this._deviceModel = null;
    this._screenSize = { width: 0, height: 0 };
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

  // ── Ghost Tap (Humanized) ─────────────────────────────────────

  /**
   * Execute a humanized tap with Gaussian jitter, variable delay,
   * and occasional micro-pauses to simulate organic behavior.
   *
   * @param {number} x - Target X coordinate
   * @param {number} y - Target Y coordinate
   * @param {Object}  [opts]
   * @param {number}  [opts.jitter=3]       - Pixel jitter σ
   * @param {boolean} [opts.prePause=true]  - Allow micro-pause before tap
   * @returns {Promise<{ x: number, y: number, jitterX: number, jitterY: number, delayMs: number, durationMs: number }>}
   */
  async ghostTap(x, y, opts = {}) {
    const jitter = opts.jitter ?? TAP_JITTER_PX;
    const prePause = opts.prePause ?? true;

    // 1. Optional micro-pause (human hesitation)
    let delayMs = 0;
    if (prePause && Math.random() < MICRO_PAUSE_PROB) {
      delayMs = randomInt(MICRO_PAUSE_RANGE[0], MICRO_PAUSE_RANGE[1]);
      await sleep(delayMs);
    }

    // 2. Gaussian jitter on target
    const jitterX = gaussianRandom() * jitter;
    const jitterY = gaussianRandom() * jitter;
    const finalX = x + jitterX;
    const finalY = y + jitterY;

    // 3. Variable pre-tap delay (reaction time)
    const reactionMs = randomInt(TAP_DELAY_RANGE[0], TAP_DELAY_RANGE[1]);
    await sleep(reactionMs);

    // 4. Execute tap
    const tapResult = await this.tap(finalX, finalY);

    const result = {
      x: tapResult.x,
      y: tapResult.y,
      jitterX: Math.round(jitterX * 10) / 10,
      jitterY: Math.round(jitterY * 10) / 10,
      delayMs: delayMs + reactionMs,
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
    if (this._dryRun) {
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

/** Promise-based sleep. */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = { AdbBridge, DEFAULT_ADB_PATHS, DEFAULT_DEVICE };

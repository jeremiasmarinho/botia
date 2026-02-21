/**
 * ADB Bootstrap — LDPlayer Environment Initializer
 *
 * Executed ONCE at Titan startup, BEFORE the YOLO model loads.
 * Forces deterministic emulator state so inference is pixel-perfect.
 *
 * Why this matters:
 *   - YOLO was trained on 640×640 crops of 1080×1920 captures.
 *     If the emulator is at 720p, every bounding box is wrong.
 *   - PPPoker PLO5/6 cards overlap heavily. A DPI mismatch of even
 *     10% causes NMS to merge two distinct cards into one detection.
 *   - Android animations add 200-400ms of transition frames where
 *     the screen is mid-fade — pure noise for the vision pipeline.
 *
 * Sequence:
 *   1. Locate adb.exe (reuses AdbBridge search paths)
 *   2. Ping device — verify LDPlayer is running
 *   3. Force resolution → 1080×1920 (portrait, PPPoker native)
 *   4. Force DPI → 320 (standard mobile density)
 *   5. Kill animations → 0.0 (instant transitions)
 *   6. Verify applied settings
 *   7. Return BootstrapResult with before/after state
 *
 * Usage:
 *   const { bootstrapEmulator } = require('./adb-bootstrap');
 *   const result = await bootstrapEmulator();
 *   if (!result.success) log.error(result.errors);
 */

"use strict";

const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const fs = require("node:fs");
const log = require("electron-log/main");

const execFileAsync = promisify(execFile);

// ── Target Configuration ────────────────────────────────────────────
// These MUST match the training data pipeline (generate_pppoker_data.py)

const TARGET = Object.freeze({
  /** Internal Android resolution — portrait mode for PPPoker */
  WIDTH: 1080,
  HEIGHT: 1920,

  /** Android DPI — 320 = standard xhdpi (PPPoker default target) */
  DPI: 320,

  /** Animation scales — 0.0 = instant (no transition frames) */
  ANIMATION_SCALE: 0.0,
});

// ── ADB Discovery ───────────────────────────────────────────────────

/** Known LDPlayer adb.exe installation paths (Windows) */
const ADB_SEARCH_PATHS = [
  "C:\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\LDPlayer\\LDPlayer4.0\\adb.exe",
  "C:\\Program Files\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\Program Files (x86)\\LDPlayer\\LDPlayer9\\adb.exe",
  "C:\\Program Files\\Genymobile\\Genymotion\\tools\\adb.exe",
];

/** Default ADB device serial for LDPlayer */
const DEFAULT_DEVICE = "emulator-5554";

/** Command timeout (ms) — generous for cold emulators */
const CMD_TIMEOUT = 8_000;

// ── Types ───────────────────────────────────────────────────────────

/**
 * @typedef {Object} EmulatorState
 * @property {{ width: number, height: number }} resolution
 * @property {number} dpi
 * @property {{ window: number, transition: number, animator: number }} animations
 */

/**
 * @typedef {Object} BootstrapResult
 * @property {boolean}       success      - All critical settings applied
 * @property {string}        device       - ADB device serial used
 * @property {string}        model        - Android device model
 * @property {EmulatorState} before       - State before bootstrap
 * @property {EmulatorState} after        - State after bootstrap
 * @property {string[]}      applied      - List of changes made
 * @property {string[]}      skipped      - Already-correct settings
 * @property {string[]}      errors       - Non-fatal errors encountered
 * @property {number}        elapsedMs    - Total bootstrap time
 */

// ── Core Bootstrap ──────────────────────────────────────────────────

/**
 * Bootstrap the LDPlayer emulator for deterministic YOLO inference.
 *
 * @param {Object}  [opts]
 * @param {string}  [opts.adbPath]   - Override adb.exe path
 * @param {string}  [opts.device]    - Override device serial
 * @param {number}  [opts.width]     - Override target width
 * @param {number}  [opts.height]    - Override target height
 * @param {number}  [opts.dpi]       - Override target DPI
 * @param {boolean} [opts.dryRun]    - Log commands without executing
 * @returns {Promise<BootstrapResult>}
 */
async function bootstrapEmulator(opts = {}) {
  const t0 = performance.now();

  const targetW = opts.width || TARGET.WIDTH;
  const targetH = opts.height || TARGET.HEIGHT;
  const targetDpi = opts.dpi || TARGET.DPI;
  const dryRun = opts.dryRun || false;

  /** @type {BootstrapResult} */
  const result = {
    success: false,
    device: "",
    model: "Unknown",
    before: emptyState(),
    after: emptyState(),
    applied: [],
    skipped: [],
    errors: [],
    elapsedMs: 0,
  };

  // ── Step 1: Find ADB ──────────────────────────────────────────
  const adbPath = opts.adbPath || findAdb();
  if (!adbPath) {
    result.errors.push(
      "adb.exe not found. Install LDPlayer or provide adbPath. " +
        `Searched: ${ADB_SEARCH_PATHS.join(", ")}, system PATH`,
    );
    result.elapsedMs = Math.round(performance.now() - t0);
    return result;
  }
  log.info(`[Bootstrap] ADB binary: ${adbPath}`);

  // ── Step 2: Ping Device ───────────────────────────────────────
  const device = await resolveDevice(adbPath, opts.device || DEFAULT_DEVICE);
  if (!device) {
    result.errors.push(
      "No online ADB device found. Is LDPlayer running with USB Debugging enabled? " +
        "Settings → About Phone → tap Build Number 7× → Developer Options → USB Debugging ON.",
    );
    result.elapsedMs = Math.round(performance.now() - t0);
    return result;
  }
  result.device = device;
  log.info(`[Bootstrap] Device: ${device}`);

  // Get device model
  try {
    const modelOut = await shell(adbPath, device, [
      "getprop",
      "ro.product.model",
    ]);
    result.model = modelOut.trim() || "Unknown";
  } catch {
    // Non-fatal
  }

  // ── Step 3: Read Current State ────────────────────────────────
  result.before = await readEmulatorState(adbPath, device);
  log.info("[Bootstrap] Current state:", JSON.stringify(result.before));

  // ── Step 4: Apply Resolution ──────────────────────────────────
  const curW = result.before.resolution.width;
  const curH = result.before.resolution.height;

  if (curW === targetW && curH === targetH) {
    result.skipped.push(`Resolution already ${targetW}x${targetH}`);
  } else {
    try {
      if (!dryRun) {
        await shell(adbPath, device, ["wm", "size", `${targetW}x${targetH}`]);
      }
      result.applied.push(
        `Resolution: ${curW}x${curH} → ${targetW}x${targetH}`,
      );
      log.info(
        `[Bootstrap] Resolution: ${curW}x${curH} → ${targetW}x${targetH}`,
      );
    } catch (err) {
      result.errors.push(`Failed to set resolution: ${err.message}`);
    }
  }

  // ── Step 5: Apply DPI ─────────────────────────────────────────
  const curDpi = result.before.dpi;

  if (curDpi === targetDpi) {
    result.skipped.push(`DPI already ${targetDpi}`);
  } else {
    try {
      if (!dryRun) {
        await shell(adbPath, device, ["wm", "density", String(targetDpi)]);
      }
      result.applied.push(`DPI: ${curDpi} → ${targetDpi}`);
      log.info(`[Bootstrap] DPI: ${curDpi} → ${targetDpi}`);
    } catch (err) {
      result.errors.push(`Failed to set DPI: ${err.message}`);
    }
  }

  // ── Step 6: Kill Animations ───────────────────────────────────
  const animKeys = [
    {
      prop: "window_animation_scale",
      label: "Window animation",
      field: "window",
    },
    {
      prop: "transition_animation_scale",
      label: "Transition animation",
      field: "transition",
    },
    {
      prop: "animator_duration_scale",
      label: "Animator duration",
      field: "animator",
    },
  ];

  for (const { prop, label, field } of animKeys) {
    const current = result.before.animations[field];

    if (current === TARGET.ANIMATION_SCALE) {
      result.skipped.push(`${label} already ${TARGET.ANIMATION_SCALE}`);
      continue;
    }

    try {
      if (!dryRun) {
        await shell(adbPath, device, [
          "settings",
          "put",
          "global",
          prop,
          String(TARGET.ANIMATION_SCALE),
        ]);
      }
      result.applied.push(`${label}: ${current} → ${TARGET.ANIMATION_SCALE}`);
      log.info(`[Bootstrap] ${label}: ${current} → ${TARGET.ANIMATION_SCALE}`);
    } catch (err) {
      // Some emulators restrict settings writes — non-fatal
      result.errors.push(`Failed to set ${prop}: ${err.message}`);
    }
  }

  // ── Step 7: Verify ────────────────────────────────────────────
  // Brief pause to let Android process the wm commands
  await sleep(500);
  result.after = await readEmulatorState(adbPath, device);

  // Validate critical settings
  const resOk =
    result.after.resolution.width === targetW &&
    result.after.resolution.height === targetH;
  const dpiOk = result.after.dpi === targetDpi;

  if (!resOk) {
    result.errors.push(
      `Resolution verification failed: expected ${targetW}x${targetH}, ` +
        `got ${result.after.resolution.width}x${result.after.resolution.height}`,
    );
  }
  if (!dpiOk) {
    result.errors.push(
      `DPI verification failed: expected ${targetDpi}, got ${result.after.dpi}`,
    );
  }

  result.success = resOk && dpiOk;
  result.elapsedMs = Math.round(performance.now() - t0);

  // ── Summary Log ───────────────────────────────────────────────
  if (result.success) {
    log.info("╔══════════════════════════════════════════════════╗");
    log.info("║       ADB BOOTSTRAP — EMULATOR CONFIGURED       ║");
    log.info("╠══════════════════════════════════════════════════╣");
    log.info(`║  Device:     ${result.device.padEnd(35)}║`);
    log.info(`║  Model:      ${result.model.padEnd(35)}║`);
    log.info(`║  Resolution: ${`${targetW}x${targetH}`.padEnd(35)}║`);
    log.info(`║  DPI:        ${String(targetDpi).padEnd(35)}║`);
    log.info(`║  Animations: ${"DISABLED (0.0)".padEnd(35)}║`);
    log.info(`║  Applied:    ${String(result.applied.length).padEnd(35)}║`);
    log.info(`║  Skipped:    ${String(result.skipped.length).padEnd(35)}║`);
    log.info(`║  Time:       ${`${result.elapsedMs}ms`.padEnd(35)}║`);
    log.info("╚══════════════════════════════════════════════════╝");
  } else {
    log.error("[Bootstrap] FAILED:", result.errors);
  }

  return result;
}

// ── Helper Functions ────────────────────────────────────────────────

/**
 * Read current resolution, DPI, and animation state from the emulator.
 * @param {string} adbPath
 * @param {string} device
 * @returns {Promise<EmulatorState>}
 */
async function readEmulatorState(adbPath, device) {
  const state = emptyState();

  // Resolution (handles both "Physical size:" and "Override size:")
  try {
    const wmSize = await shell(adbPath, device, ["wm", "size"]);
    // Prefer override if set, otherwise physical
    const overrideMatch = wmSize.match(/Override size:\s*(\d+)x(\d+)/);
    const physicalMatch = wmSize.match(/Physical size:\s*(\d+)x(\d+)/);
    const match = overrideMatch || physicalMatch;
    if (match) {
      state.resolution.width = parseInt(match[1], 10);
      state.resolution.height = parseInt(match[2], 10);
    }
  } catch {
    // Leave at 0×0
  }

  // DPI (handles both "Physical density:" and "Override density:")
  try {
    const wmDensity = await shell(adbPath, device, ["wm", "density"]);
    const overrideMatch = wmDensity.match(/Override density:\s*(\d+)/);
    const physicalMatch = wmDensity.match(/Physical density:\s*(\d+)/);
    const match = overrideMatch || physicalMatch;
    if (match) {
      state.dpi = parseInt(match[1], 10);
    }
  } catch {
    // Leave at 0
  }

  // Animations
  for (const [prop, field] of [
    ["window_animation_scale", "window"],
    ["transition_animation_scale", "transition"],
    ["animator_duration_scale", "animator"],
  ]) {
    try {
      const val = await shell(adbPath, device, [
        "settings",
        "get",
        "global",
        prop,
      ]);
      const parsed = parseFloat(val.trim());
      state.animations[field] = Number.isFinite(parsed) ? parsed : 1.0;
    } catch {
      state.animations[field] = 1.0; // Android default
    }
  }

  return state;
}

/**
 * Resolve which ADB device to use. Verifies it's online.
 * @param {string} adbPath
 * @param {string} preferredDevice
 * @returns {Promise<string|null>}
 */
async function resolveDevice(adbPath, preferredDevice) {
  try {
    const { stdout } = await execFileAsync(adbPath, ["devices"], {
      timeout: CMD_TIMEOUT,
      windowsHide: true,
    });

    const lines = stdout.trim().split("\n").slice(1); // skip header
    const online = lines
      .map((l) => l.trim().split(/\s+/))
      .filter((parts) => parts.length >= 2 && parts[1] === "device")
      .map((parts) => parts[0]);

    if (online.includes(preferredDevice)) {
      return preferredDevice;
    }

    // Auto-select first available
    if (online.length > 0) {
      log.warn(
        `[Bootstrap] Preferred device ${preferredDevice} not found, using ${online[0]}`,
      );
      return online[0];
    }

    return null;
  } catch (err) {
    log.error(`[Bootstrap] ADB devices query failed: ${err.message}`);
    return null;
  }
}

/**
 * Find adb.exe from known paths or system PATH.
 * @returns {string|null}
 */
function findAdb() {
  for (const candidate of ADB_SEARCH_PATHS) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  // Fallback: system PATH
  try {
    const { execFileSync } = require("node:child_process");
    execFileSync("adb", ["version"], { timeout: 3000, windowsHide: true });
    return "adb";
  } catch {
    return null;
  }
}

/**
 * Execute a shell command on the device.
 * @param {string} adbPath
 * @param {string} device
 * @param {string[]} args
 * @returns {Promise<string>}
 */
async function shell(adbPath, device, args) {
  const { stdout } = await execFileAsync(
    adbPath,
    ["-s", device, "shell", ...args],
    { timeout: CMD_TIMEOUT, windowsHide: true },
  );
  return stdout;
}

/** @returns {EmulatorState} */
function emptyState() {
  return {
    resolution: { width: 0, height: 0 },
    dpi: 0,
    animations: { window: 1.0, transition: 1.0, animator: 1.0 },
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = { bootstrapEmulator, TARGET, ADB_SEARCH_PATHS };

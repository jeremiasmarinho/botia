/**
 * Shadow Mode — Standalone Electron Entry Point (Hero Reconnaissance)
 *
 * A SEPARATE Electron main script (not main.js) that boots only the
 * components needed for passive visual reconnaissance:
 *
 *   ✅ adb-bootstrap  — force LDPlayer resolution/DPI/animations
 *   ✅ ADB connect     — verify emulator is reachable (NO taps)
 *   ✅ Inference window — hidden BrowserWindow, WebGPU YOLO
 *   ✅ GameLoop         — runs WAITING → PERCEPTION, but NEVER
 *                         advances to CALCULATING or EXECUTING
 *   ❌ Dashboard        — not created (no UI needed)
 *   ❌ SolverBridge     — not loaded (no equity needed)
 *   ❌ OpponentDb       — not loaded (no profiling needed)
 *   ❌ ADB taps         — completely disabled
 *
 * When the Stability Gate fires (3 consecutive frames with identical
 * card signatures), we log the frozen detections and reset to WAITING.
 * The GameLoop never touches CALCULATING, EXECUTING, or COOLDOWN.
 *
 * Launch:
 *   npx electron src/main/shadow-mode.js
 *   npm run shadow
 *   start_shadow.bat
 *
 * Output:
 *   [SHADOW MODE] Mesa Estável! Hero: [Ah, Kd, Jc, 4s, 2c] | Board: [Ts, 9h, 2d] | Confiança: 96% | Latência YOLO: 18ms
 */

"use strict";

const { app, BrowserWindow, ipcMain, desktopCapturer } = require("electron");
const path = require("node:path");
const log = require("electron-log/main");

const IPC = require("../shared/ipc-channels");
const { bootstrapEmulator } = require("./execution/adb-bootstrap");
const { AdbBridge } = require("./execution/adb-bridge");
const { GameLoop, LoopState } = require("./game-loop");

// ── GPU Cache Fix ───────────────────────────────────────────────────
// Chromium tries to create a GPU shader disk cache in a dir that may
// lack write permissions (Windows 'Acesso negado' / Access Denied).
// Disable the GPU shader disk cache entirely (not needed for headless
// inference) and redirect the general disk cache to userData.
app.commandLine.appendSwitch("disable-gpu-shader-disk-cache");
app.commandLine.appendSwitch(
  "disk-cache-dir",
  path.join(app.getPath("userData"), "gpu-cache"),
);

// ── GPU Stability Flags ─────────────────────────────────────────────
// Disable WGC (Windows Graphics Capture) which crashes on some GPU
// contexts (LDPlayer). Fall back to DXGI Desktop Duplication.
app.commandLine.appendSwitch("disable-features", "WgcCapturerWin");
app.commandLine.appendSwitch("disable-gpu-sandbox");

// ── Logging Configuration ───────────────────────────────────────────

log.transports.file.level = "info";
log.transports.console.level = "info"; // Terminal must be visible

// ── Class Name Mapping ──────────────────────────────────────────────

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

// ── ANSI Terminal Colors ────────────────────────────────────────────

const SUIT_SYMBOLS = { c: "♣", d: "♦", h: "♥", s: "♠" };
const SUIT_COLORS = {
  c: "\x1b[32m", // green  (clubs)
  d: "\x1b[34m", // blue   (diamonds)
  h: "\x1b[31m", // red    (hearts)
  s: "\x1b[37m", // white  (spades)
};

const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RED = "\x1b[31m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const CYAN = "\x1b[36m";
const MAGENTA = "\x1b[35m";

// ── Card/Button Formatting ──────────────────────────────────────────

/**
 * Short poker notation for a classId (e.g. 50 → "Ah").
 * @param {number} classId
 * @returns {string}
 */
function cardName(classId) {
  return CLASS_NAMES[classId] || `?${classId}`;
}

/**
 * Colorized terminal card string (e.g. classId 50 → red "A♥ 96%").
 * @param {number} classId
 * @param {number} [confidence]
 * @returns {string}
 */
function formatCard(classId, confidence) {
  const name = CLASS_NAMES[classId];
  if (!name || classId > 51) return `?${classId}`;

  const rank = name[0];
  const suit = name[1];
  const symbol = SUIT_SYMBOLS[suit] || suit;
  const color = SUIT_COLORS[suit] || "";
  const confStr =
    confidence != null
      ? ` ${DIM}${(confidence * 100).toFixed(0)}%${RESET}`
      : "";

  return `${color}${BOLD}${rank}${symbol}${RESET}${confStr}`;
}

/**
 * Colorized terminal button string (e.g. classId 52 → yellow "FOLD").
 * @param {number} classId
 * @returns {string}
 */
function formatButton(classId) {
  const name = CLASS_NAMES[classId] || `btn_${classId}`;
  return `${YELLOW}${name.toUpperCase()}${RESET}`;
}

// ── Hero Region Threshold (normalized, matches game-loop.js) ────────

const HERO_Y_THRESHOLD = 0.65;

// ── Global References ───────────────────────────────────────────────

/** @type {BrowserWindow} Hidden GPU inference window */
let inferenceWindow = null;

/** @type {AdbBridge} */
let adb = null;

/** @type {GameLoop} */
let gameLoop = null;

/** @type {string|null} Stored LDPlayer source ID for resumeVision */
let activeLdSourceId = null;

/** @type {number} Total stable readings logged */
let stableReadings = 0;

/** @type {number} FPS tracking */
let fpsFrameCount = 0;
let fpsTimestamp = 0;
let currentFps = 0;

// ══════════════════════════════════════════════════════════════════════
// ── App Lifecycle ─────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════

app.whenReady().then(async () => {
  printBanner();

  // ── GPU crash resilience ────────────────────────────────────────
  app.on("gpu-info-update", () => {});
  app.on("child-process-gone", (_event, details) => {
    if (details.type === "GPU") {
      log.warn(
        `[Shadow] GPU process gone (reason=${details.reason}). Continuing...`,
      );
    }
  });

  // ── 1. Bootstrap emulator (resolution + DPI) ──────────────────
  log.info("[Shadow] Bootstrapping LDPlayer...");
  try {
    const result = await bootstrapEmulator();
    if (result.success) {
      log.info(
        `[Shadow] ${GREEN}✓${RESET} Emulator: ${result.device} ` +
          `${result.after.resolution} @ ${result.after.dpi}dpi ` +
          `animations=${result.after.animations}`,
      );
    } else {
      log.warn(
        `[Shadow] ${YELLOW}⚠${RESET} Bootstrap partial — ` +
          `errors: ${result.errors.join(", ")}`,
      );
    }
  } catch (err) {
    log.warn(`[Shadow] ${RED}✗${RESET} Bootstrap failed: ${err.message}`);
    log.warn("[Shadow] Continuing without bootstrap — vision may be degraded.");
  }

  // ── 2. Connect ADB (verify only — NO taps ever) ──────────────
  log.info("[Shadow] Connecting ADB (dry-run)...");
  try {
    adb = new AdbBridge({ dryRun: true });
    const info = await adb.connect();
    log.info(
      `[Shadow] ${GREEN}✓${RESET} ADB: ${info.device} (${info.model}) ` +
        `${info.screen.width}×${info.screen.height} ${DIM}[dry-run — no taps]${RESET}`,
    );
  } catch (err) {
    log.warn(`[Shadow] ${RED}✗${RESET} ADB: ${err.message}`);
    log.warn("[Shadow] Continuing without ADB — detection-only mode.");
    adb = null;
  }

  // ── 3. Create inference BrowserWindow (no dashboard) ──────────
  log.info("[Shadow] Creating inference window (WebGPU YOLO)...");
  inferenceWindow = createInferenceWindow();

  // ── 4. Register IPC handlers (vision only) ────────────────────
  registerShadowIpc();

  // ── 5. Init GameLoop with Stability Gate interception ─────────
  initShadowGameLoop();

  log.info("");
  log.info(
    `[Shadow] ${GREEN}${BOLD}Ready.${RESET} ` +
      `Waiting for vision frames from inference window...`,
  );
  log.info(
    `[Shadow] Open a PLO5/PLO6 table in LDPlayer. ` +
      `Cards will appear below when detected.`,
  );
  log.info("");
});

app.on("window-all-closed", () => {
  log.info("[Shadow] Shutting down...");
  if (gameLoop) gameLoop.stop();
  if (inferenceWindow && !inferenceWindow.isDestroyed()) {
    inferenceWindow.destroy();
  }
  app.quit();
});

// ══════════════════════════════════════════════════════════════════════
// ── Inference Window ──────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════

function createInferenceWindow() {
  const win = new BrowserWindow({
    width: 1,
    height: 1,
    show: false,
    skipTaskbar: true,
    title: "Titan Shadow — Inference",
    webPreferences: {
      preload: path.join(__dirname, "preload-inference.js"),
      contextIsolation: true,
      nodeIntegration: false,
      experimentalFeatures: true, // WebGPU
      offscreen: false, // Need real GPU context
      backgroundThrottling: false, // CRITICAL: prevent Chrome throttling
    },
  });

  win.loadFile(path.join(__dirname, "..", "renderer", "inference.html"));

  // Forward renderer console to main process for debugging
  win.webContents.on(
    "console-message",
    (_event, level, message, line, sourceId) => {
      const tag = ["LOG", "WARN", "ERR"][level] || "LOG";
      log.info(`[InferenceRenderer][${tag}] ${message}`);
    },
  );

  win.webContents.on("crashed", (_event, killed) => {
    log.error(
      `[Shadow] ${RED}Inference renderer crashed${RESET} (killed=${killed}).`,
    );
  });

  return win;
}

// ══════════════════════════════════════════════════════════════════════
// ── IPC Handlers (minimal — vision only) ──────────────────────────────
// ══════════════════════════════════════════════════════════════════════

function registerShadowIpc() {
  // ── Source enumeration (inference window needs this to find LDPlayer) ──
  ipcMain.handle(IPC.GET_SOURCES, async () => {
    const sources = await desktopCapturer.getSources({
      types: ["window"],
      thumbnailSize: { width: 0, height: 0 },
    });
    return sources
      .filter((s) => s.name.toLowerCase().includes("ldplayer"))
      .map((s) => ({ id: s.id, name: s.name }));
  });

  // ── Vision detections → feed into GameLoop ────────────────────
  let visionLogCounter = 0;
  ipcMain.on(IPC.VISION_DETECTIONS, (_event, payload) => {
    // FPS counter
    fpsFrameCount++;
    const now = performance.now();
    if (now - fpsTimestamp >= 1000) {
      currentFps = fpsFrameCount;
      fpsFrameCount = 0;
      fpsTimestamp = now;
    }

    // Log detection summary every ~5 seconds (25 frames @ 5fps)
    visionLogCounter++;
    if (payload.detections?.length > 0 && visionLogCounter % 25 === 1) {
      const labels = payload.detections.map(
        (d) => `${d.label}(${(d.confidence * 100).toFixed(0)}%)`,
      );
      log.info(
        `[Shadow] ${DIM}Detections: [${labels.join(", ")}] ` +
          `${payload.inferenceMs}ms ${currentFps}fps${RESET}`,
      );
    }

    // Feed into GameLoop (intercepted at Stability Gate)
    if (gameLoop?.running) {
      gameLoop.onVisionFrame(payload);
    }
  });

  // ── Vision status → auto-start capture when model is ready ───
  ipcMain.on(IPC.VISION_STATUS, async (_event, status) => {
    log.info(
      `[Shadow] ${GREEN}✓${RESET} YOLO engine ready: ` +
        `backend=${BOLD}${status.backend}${RESET} ` +
        `classes=${status.modelClasses}`,
    );

    if (!status.ready) return;

    // Auto-find LDPlayer window and start capturing
    try {
      const sources = await desktopCapturer.getSources({
        types: ["window"],
        thumbnailSize: { width: 0, height: 0 },
      });

      const ldSource = sources.find((s) =>
        s.name.toLowerCase().includes("ldplayer"),
      );

      if (ldSource && inferenceWindow && !inferenceWindow.isDestroyed()) {
        activeLdSourceId = ldSource.id; // Store for resumeVision()
        log.info(
          `[Shadow] ${GREEN}✓${RESET} Found LDPlayer window: ` +
            `"${ldSource.name}" (${ldSource.id})`,
        );
        inferenceWindow.webContents.send(IPC.VISION_START, {
          sourceId: ldSource.id,
          fps: 5,
        });
        log.info(`[Shadow] ${GREEN}▶${RESET} Vision capture started @ 5 FPS`);
      } else {
        log.warn(
          `[Shadow] ${YELLOW}⚠${RESET} LDPlayer window not found. ` +
            `Open LDPlayer and restart shadow mode.`,
        );
        // List available windows for debugging
        const names = sources.map((s) => s.name).slice(0, 10);
        log.warn(`[Shadow] Available windows: ${names.join(", ")}`);
      }
    } catch (err) {
      log.error(
        `[Shadow] ${RED}✗${RESET} Auto-start capture failed: ${err.message}`,
      );
    }
  });

  // ── Vision error ──────────────────────────────────────────────
  ipcMain.on(IPC.VISION_ERROR, (_event, err) => {
    log.error(
      `[Shadow] ${RED}Vision error:${RESET} ${err.error} (fatal=${err.fatal})`,
    );
  });

  // ── Vision start/stop/config (forwarding to renderer) ─────────
  ipcMain.handle(IPC.VISION_START, async (_event, opts) => {
    if (inferenceWindow && !inferenceWindow.isDestroyed()) {
      inferenceWindow.webContents.send(IPC.VISION_START, opts || {});
      return { ok: true };
    }
    return { error: "Inference window not available" };
  });

  ipcMain.handle(IPC.VISION_STOP, async () => {
    if (inferenceWindow && !inferenceWindow.isDestroyed()) {
      inferenceWindow.webContents.send(IPC.VISION_STOP);
    }
    return { ok: true };
  });

  ipcMain.handle(IPC.VISION_CONFIG, async (_event, config) => {
    if (inferenceWindow && !inferenceWindow.isDestroyed()) {
      inferenceWindow.webContents.send(IPC.VISION_CONFIG, config);
    }
    return { ok: true };
  });
}

// ══════════════════════════════════════════════════════════════════════
// ── Shadow GameLoop (Stability Gate Interception) ─────────────────────
// ══════════════════════════════════════════════════════════════════════

/**
 * Creates a GameLoop with NULL stubs for solver, GtoEngine, opponentDb.
 * ADB is connected in dry-run mode (bootstrap verification only).
 *
 * INTERCEPTION MECHANISM:
 *   We monkey-patch `_runCalculation()` to be a no-op that logs the
 *   frozen detections and transitions straight back to WAITING.
 *   This guarantees CALCULATING, EXECUTING, and COOLDOWN are
 *   completely unreachable — the loop ping-pongs between WAITING
 *   and PERCEPTION only.
 *
 * Why monkey-patch instead of subclass?
 *   GameLoop's `_handlePerception()` calls `_runCalculation()` directly
 *   (not via an overridable hook).  A subclass would need to duplicate
 *   the entire perception logic.  The patch is surgical: it only
 *   replaces the ONE method that transitions out of PERCEPTION.
 */
function initShadowGameLoop() {
  // Stub dependencies — constructor requires these but they're never called
  const stubAdb = {
    connected: adb?.connected || false,
    executeAction: async () => ({ dropped: true, reason: "shadow_mode" }),
  };
  const stubSolver = { initialized: false };
  const stubGtoEngine = {
    decide: () => ({
      action: "fold",
      confidence: 0,
      raiseSize: 0,
      reasoning: "shadow_mode",
    }),
  };

  gameLoop = new GameLoop({
    adb: stubAdb,
    solver: stubSolver,
    GtoEngine: stubGtoEngine,
    opponentDb: null,
    log,
    setVisionFps: (fps) => {
      if (inferenceWindow && !inferenceWindow.isDestroyed()) {
        inferenceWindow.webContents.send(IPC.VISION_CONFIG, { fps });
      }
    },
    pauseVision: () => {
      if (inferenceWindow && !inferenceWindow.isDestroyed()) {
        inferenceWindow.webContents.send(IPC.VISION_STOP);
      }
    },
    resumeVision: () => {
      // Re-send VISION_START with the stored sourceId (not undefined!)
      if (
        inferenceWindow &&
        !inferenceWindow.isDestroyed() &&
        activeLdSourceId
      ) {
        inferenceWindow.webContents.send(IPC.VISION_START, {
          sourceId: activeLdSourceId,
          fps: 5,
        });
      }
    },
  });

  // ── INTERCEPTION: Override _runCalculation() ──────────────────
  //
  // Original flow: Stability Gate → _transitionTo(CALCULATING) → _runCalculation()
  //                → solver → GtoEngine → _transitionTo(EXECUTING) → ADB tap
  //
  // Shadow flow:   Stability Gate → _transitionTo(CALCULATING) → _runCalculation()
  //                → log frozen detections → _transitionTo(WAITING) → done
  //
  // Net effect: CALCULATING is entered for ~0ms, then immediately exits
  //             to WAITING.  EXECUTING and COOLDOWN are never reached.

  gameLoop._runCalculation = function () {
    if (!this._running || this._state !== LoopState.CALCULATING) return;

    const frozen = this._frozenDetections;
    if (!frozen) {
      this._transitionTo(LoopState.WAITING);
      return;
    }

    stableReadings++;
    logStableReading(frozen, stableReadings);

    // Skip EXECUTING and COOLDOWN — return to observation
    this._transitionTo(LoopState.WAITING);
  };

  // ── Start the loop ────────────────────────────────────────────
  gameLoop.start();
  log.info(`[Shadow] GameLoop started (observation mode — WAITING @ 5 FPS)`);
}

// ══════════════════════════════════════════════════════════════════════
// ── Stable Reading Logger ─────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════

/**
 * Log a stable reading in the trainer-specified format:
 *
 *   [SHADOW MODE] Mesa Estável! Hero: [Ah, Kd, Jc, 4s, 2c] | Board: [Ts, 9h, 2d] | Confiança: 96% | Latência YOLO: 18ms
 *
 * Only fires when the Stability Gate passes (3+ identical frames).
 * No frame spam — this is the ONLY output during normal operation.
 *
 * @param {Object} frozen - The frozenDetections from GameLoop
 * @param {number} readingNum - Sequential reading number
 */
function logStableReading(frozen, readingNum) {
  const { cards, buttons, inferenceMs } = frozen;

  // ── Separate hero cards from board cards ──────────────────────
  const heroCards = cards
    .filter((c) => c.classId <= 51 && c.cy > HERO_Y_THRESHOLD)
    .sort((a, b) => a.cx - b.cx);

  const boardCards = cards
    .filter((c) => c.classId <= 51 && c.cy <= HERO_Y_THRESHOLD)
    .sort((a, b) => a.cx - b.cx);

  const actionButtons = buttons
    .filter((b) => b.classId >= 52 && b.classId <= 61)
    .sort((a, b) => a.cx - b.cx);

  // ── Average confidence ────────────────────────────────────────
  const allDetections = [...heroCards, ...boardCards, ...actionButtons];
  const avgConf =
    allDetections.length > 0
      ? allDetections.reduce((sum, d) => sum + (d.confidence || 0), 0) /
        allDetections.length
      : 0;

  // ── Card name lists (plain text) ──────────────────────────────
  const heroList = heroCards.map((c) => cardName(c.classId)).join(", ") || "—";
  const boardList =
    boardCards.map((c) => cardName(c.classId)).join(", ") || "—";
  const buttonList =
    actionButtons.map((b) => formatButton(b.classId)).join(", ") || "—";

  // ── Variant + Street ──────────────────────────────────────────
  const variant =
    heroCards.length >= 6
      ? "PLO6"
      : heroCards.length >= 5
        ? "PLO5"
        : `${heroCards.length}c`;
  const streetMap = { 0: "Preflop", 3: "Flop", 4: "Turn", 5: "River" };
  const street = streetMap[boardCards.length] || `Board(${boardCards.length})`;

  // ── Memory ────────────────────────────────────────────────────
  const heapMb = (process.memoryUsage().heapUsed / 1024 / 1024).toFixed(0);

  // ── Primary log line (trainer-specified format) ───────────────
  log.info(
    `${CYAN}${BOLD}[SHADOW MODE]${RESET} ` +
      `${GREEN}Mesa Estável!${RESET} ` +
      `Hero: [${heroList}] | ` +
      `Board: [${boardList}] | ` +
      `Confiança: ${(avgConf * 100).toFixed(0)}% | ` +
      `Latência YOLO: ${inferenceMs}ms`,
  );

  // ── Detail line (colorized cards + buttons + metadata) ────────
  const heroColorized = heroCards
    .map((c) => formatCard(c.classId, c.confidence))
    .join(" ");
  const boardColorized = boardCards
    .map((c) => formatCard(c.classId, c.confidence))
    .join(" ");

  log.info(
    `  ${DIM}#${readingNum}${RESET}  ` +
      `${MAGENTA}${variant}${RESET} ${DIM}${street}${RESET}  ` +
      `Hero: [${heroColorized || `${DIM}—${RESET}`}]  ` +
      `Board: [${boardColorized || `${DIM}—${RESET}`}]  ` +
      `Buttons: [${buttonList}]  ` +
      `${DIM}${currentFps}fps  ${heapMb}MB${RESET}`,
  );

  // ── Bounding box detail (debug level only) ────────────────────
  for (const card of [...heroCards, ...boardCards]) {
    const region = card.cy > HERO_Y_THRESHOLD ? "hero" : "board";
    log.debug(
      `  ${DIM}bbox [${region}]${RESET}  ${cardName(card.classId)}  ` +
        `cx=${Math.round(card.cx)} cy=${Math.round(card.cy)} ` +
        `w=${Math.round(card.w)} h=${Math.round(card.h)} ` +
        `conf=${(card.confidence * 100).toFixed(1)}%`,
    );
  }
  for (const btn of actionButtons) {
    log.debug(
      `  ${DIM}bbox [button]${RESET}  ${CLASS_NAMES[btn.classId]}  ` +
        `cx=${Math.round(btn.cx)} cy=${Math.round(btn.cy)} ` +
        `w=${Math.round(btn.w)} h=${Math.round(btn.h)} ` +
        `conf=${(btn.confidence * 100).toFixed(1)}%`,
    );
  }

  log.info(""); // blank line separator between readings
}

// ══════════════════════════════════════════════════════════════════════
// ── Startup Banner ────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════

function printBanner() {
  log.info("");
  log.info(
    `${BOLD}${CYAN}╔════════════════════════════════════════════════════════════════╗${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║          TITAN EDGE AI — SHADOW MODE (RECONNAISSANCE)         ║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}╠════════════════════════════════════════════════════════════════╣${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  ${GREEN}●${RESET} ADB Bootstrap:    Force 1080×1920 @ 320dpi             ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  ${GREEN}●${RESET} Inference Window:  WebGPU YOLO (hidden BrowserWindow)  ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  ${GREEN}●${RESET} GameLoop:          WAITING → PERCEPTION only           ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  ${GREEN}●${RESET} Stability Gate:    3 frames → log → reset             ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  ${RED}●${RESET} CALCULATING:       ${RED}${BOLD}BLOCKED${RESET}                            ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  ${RED}●${RESET} EXECUTING:         ${RED}${BOLD}BLOCKED${RESET}                            ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  ${RED}●${RESET} ADB Taps:          ${RED}${BOLD}DISABLED (dry-run)${RESET}                 ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}╠════════════════════════════════════════════════════════════════╣${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}║${RESET}  Press Ctrl+C to exit                                       ${BOLD}${CYAN}║${RESET}`,
  );
  log.info(
    `${BOLD}${CYAN}╚════════════════════════════════════════════════════════════════╝${RESET}`,
  );
  log.info("");
}

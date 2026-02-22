/**
 * Titan Edge AI — Electron Main Process (v2: Rust N-API + WebGPU)
 *
 * Orchestrates all subsystems with HFT-grade latency targets:
 *   - SolverBridge: Rust N-API addon (target <3ms equity)
 *   - Inference Window: Hidden BrowserWindow (TF.js + WebGPU YOLO)
 *   - Opponent DB: SQLite (better-sqlite3, variant-isolated)
 *   - ADB Bridge: Action execution on LDPlayer
 *
 * Process Architecture:
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │                    Main Process (Node.js)                       │
 *   │  ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
 *   │  │ SolverBridge │ │ AdbBridge│ │OpponentDb│ │  GtoEngine  │   │
 *   │  │ (Rust N-API) │ │ (ADB)    │ │ (SQLite) │ │ (Decisions) │   │
 *   │  │  <3ms equity │ │          │ │ WAL mode │ │             │   │
 *   │  └──────┬───────┘ └────┬─────┘ └────┬─────┘ └──────┬──────┘   │
 *   │         │              │             │              │          │
 *   │         └──────────────┼─────────────┼──────────────┘          │
 *   │                        │             │                         │
 *   └────────────────────────┼─────────────┼─────────────────────────┘
 *          IPC (detections)  │             │  IPC (dashboard)
 *   ┌────────────────────────┼─────┐ ┌─────┼─────────────────────────┐
 *   │    Hidden Inference    │     │ │     │    Dashboard Window     │
 *   │    BrowserWindow       │     │ │     │    (Chromium UI)        │
 *   │  ┌────────────────────┐│     │ │     │                         │
 *   │  │ TF.js + WebGPU    ││     │ │     │  Stats, Cards, HUD      │
 *   │  │ YOLO v8 @ ~30ms   ││     │ │     │                         │
 *   │  └────────────────────┘│     │ │     │                         │
 *   └────────────────────────┘     │ └─────────────────────────────────┘
 */

"use strict";

const { app, BrowserWindow, ipcMain, desktopCapturer } = require("electron");
const path = require("node:path");
const log = require("electron-log/main");

const IPC = require("../shared/ipc-channels");
const { bootstrapEmulator } = require("./execution/adb-bootstrap");
const { AdbBridge } = require("./execution/adb-bridge");
const { SolverBridge, GameVariant } = require("./brain/solver-bridge");
const { GtoEngine } = require("./brain/gto-engine");
const { OpponentDb } = require("./profiling/opponent-db");
const { GameLoop } = require("./game-loop");

// ── Configuration ───────────────────────────────────────────────────

const IS_DEV = process.env.NODE_ENV === "development";

log.transports.file.level = "info";
log.transports.console.level = IS_DEV ? "debug" : "warn";

// ── Global References ───────────────────────────────────────────────

/** @type {BrowserWindow} Dashboard UI */
let mainWindow = null;

/** @type {BrowserWindow} Hidden GPU inference window */
let inferenceWindow = null;

/** @type {AdbBridge} */
let adb = null;

/** @type {SolverBridge} Rust N-API addon bridge */
let solver = null;

/** @type {OpponentDb} */
let opponentDb = null;

/** @type {GameLoop} Centralized state machine */
let gameLoop = null;

/** @type {import('./execution/adb-bootstrap').BootstrapResult|null} */
let bootstrapResult = null;

/** Latest vision detections (updated by inference window) */
let lastVisionResult = null;

/** Stored LDPlayer source ID for resumeVision (prevents sourceId: undefined crash) */
let activeLdSourceId = null;

// ── App Lifecycle ───────────────────────────────────────────────────

app.whenReady().then(async () => {
  log.info("[Titan] Starting Edge AI v2 — Rust N-API + WebGPU");

  // 0. Bootstrap emulator (resolution + DPI + animations)
  //    MUST run BEFORE inference window loads the YOLO model
  bootstrapResult = await bootstrapEmulator();
  if (!bootstrapResult.success) {
    log.warn("[Titan] Emulator bootstrap incomplete — vision may be degraded");
    log.warn("[Titan] Errors:", bootstrapResult.errors);
  }

  // 1. Create windows
  mainWindow = createDashboardWindow();
  inferenceWindow = createInferenceWindow();

  // 2. Initialize subsystems in parallel
  await Promise.allSettled([initAdb(), initSolver(), initOpponentDb()]);

  // 3. Register IPC handlers
  registerIpcHandlers();

  // 4. Initialize Game Loop (AFTER all subsystems are ready)
  initGameLoop();

  log.info("[Titan] All systems initialized.");
  logSystemSummary();
});

app.on("window-all-closed", async () => {
  log.info("[Titan] Shutting down...");
  await shutdown();
  app.quit();
});

// Prevent the hidden window from keeping the app alive on its own
app.on("before-quit", () => {
  if (inferenceWindow && !inferenceWindow.isDestroyed()) {
    inferenceWindow.destroy();
  }
});

// ── Window Creation ─────────────────────────────────────────────────

/**
 * Create the main dashboard window (visible UI).
 */
function createDashboardWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "Titan Edge AI — Omaha PLO5/PLO6",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      experimentalFeatures: true,
    },
    backgroundColor: "#1a1a2e",
    show: false,
  });

  win.loadFile(path.join(__dirname, "..", "renderer", "index.html"));

  win.once("ready-to-show", () => {
    win.show();
    if (IS_DEV) win.webContents.openDevTools({ mode: "detach" });
  });

  return win;
}

/**
 * Create the hidden inference window (WebGPU YOLO engine).
 *
 * This window is invisible but runs a full Chromium renderer with
 * WebGPU access. TF.js loads the YOLO model and processes screen
 * captures at ~30ms per frame on RTX 2060 Super.
 *
 * Why a hidden window? WebGPU is only available in renderer processes.
 * A Worker Thread cannot access the GPU. This is the fastest path.
 */
function createInferenceWindow() {
  const win = new BrowserWindow({
    width: 1,
    height: 1,
    show: false,
    skipTaskbar: true,
    title: "Titan Inference Engine",
    webPreferences: {
      preload: path.join(__dirname, "preload-inference.js"),
      contextIsolation: true,
      nodeIntegration: false,
      experimentalFeatures: true, // WebGPU
      offscreen: false, // Need real GPU context
      backgroundThrottling: false, // CRITICAL: prevent Chrome throttling hidden windows
    },
  });

  // Load the minimal inference HTML page
  win.loadFile(path.join(__dirname, "..", "renderer", "inference.html"));

  win.webContents.on("crashed", (_event, killed) => {
    log.error(`[Inference] Renderer crashed (killed=${killed}). Restarting...`);
    restartInferenceWindow();
  });

  return win;
}

/**
 * Restart inference window after crash.
 */
function restartInferenceWindow() {
  if (inferenceWindow && !inferenceWindow.isDestroyed()) {
    inferenceWindow.destroy();
  }
  inferenceWindow = createInferenceWindow();
  log.info("[Inference] Window restarted.");
}

// ── Subsystem Initialization ────────────────────────────────────────

async function initAdb() {
  try {
    adb = new AdbBridge();
    adb.on("connected", (info) => {
      log.info(
        `[ADB] Connected: ${info.device} (${info.model}) ${info.screen.width}x${info.screen.height}`,
      );
    });
    adb.on("tap", (r) => {
      log.debug(`[ADB] Tap: (${r.x}, ${r.y}) ${r.durationMs}ms`);
    });
    adb.on("warn", (msg) => log.warn(`[ADB] ${msg}`));

    const info = await adb.connect();
    mainWindow?.webContents.send(IPC.ADB_STATUS, { connected: true, ...info });
  } catch (err) {
    log.warn(`[ADB] Not available: ${err.message}`);
    mainWindow?.webContents.send(IPC.ADB_STATUS, {
      connected: false,
      error: err.message,
    });
  }
}

/**
 * Initialize the Rust N-API SolverBridge.
 * Replaces the old EquityPool (JS Worker Threads @ 173ms → target <3ms).
 */
async function initSolver() {
  try {
    solver = new SolverBridge();
    solver.on("ready", ({ native, version }) => {
      log.info(`[Solver] Ready — engine=${version} native=${native}`);
      if (!native) {
        log.warn(
          "[Solver] ⚠ Running JS fallback — 50x slower than Rust N-API!",
        );
        log.warn(
          "[Solver]   Build titan_core.node: cd core-engine && cargo build --release",
        );
      }
    });
    await solver.init();
  } catch (err) {
    log.error(`[Solver] Init failed: ${err.message}`);
  }
}

async function initOpponentDb() {
  try {
    const dbPath = path.join(app.getPath("userData"), "db", "opponents.db");
    opponentDb = new OpponentDb(dbPath);
    opponentDb.init();
    log.info(`[DB] Opponent database ready: ${dbPath}`);
  } catch (err) {
    log.error(`[DB] Init failed: ${err.message}`);
  }
}

// ── Game Loop Initialization ────────────────────────────────────────

function initGameLoop() {
  gameLoop = new GameLoop({
    adb,
    solver,
    GtoEngine,
    opponentDb,
    log,

    /**
     * Control inference window capture FPS.
     * Sends VISION_CONFIG to the hidden BrowserWindow.
     */
    setVisionFps: (fps) => {
      if (inferenceWindow && !inferenceWindow.isDestroyed()) {
        inferenceWindow.webContents.send(IPC.VISION_CONFIG, { fps });
      }
    },

    /** Pause vision capture (freeze during CALCULATING/EXECUTING). */
    pauseVision: () => {
      if (inferenceWindow && !inferenceWindow.isDestroyed()) {
        inferenceWindow.webContents.send(IPC.VISION_STOP);
      }
    },

    /** Resume vision capture (re-enter WAITING). */
    resumeVision: () => {
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

  // ── Forward Game Loop events to Dashboard ─────────────────────
  gameLoop.on("stateChange", (data) => {
    mainWindow?.webContents.send(IPC.GAMELOOP_STATE, data);
    log.debug(`[GameLoop] ${data.previousState || "INIT"} → ${data.state}`);
  });

  gameLoop.on("cycleComplete", (data) => {
    mainWindow?.webContents.send(IPC.GAMELOOP_CYCLE, data);
  });

  gameLoop.on("perception", (data) => {
    mainWindow?.webContents.send(IPC.GAMELOOP_PERCEPTION, data);
  });

  gameLoop.on("actionExecuted", (data) => {
    mainWindow?.webContents.send(IPC.ADB_TAP_RESULT, data);
  });

  log.info("[GameLoop] Initialized — ready to start via IPC");
}

// ── IPC Handlers ────────────────────────────────────────────────────

function registerIpcHandlers() {
  // ── Screen Capture Sources (for inference window) ─────────────
  ipcMain.handle(IPC.GET_SOURCES, async () => {
    const sources = await desktopCapturer.getSources({
      types: ["window"],
      thumbnailSize: { width: 0, height: 0 },
    });
    return sources
      .filter((s) => s.name.toLowerCase().includes("ldplayer"))
      .map((s) => ({ id: s.id, name: s.name }));
  });

  // ── Equity Calculation (Rust N-API) ───────────────────────────
  ipcMain.handle(IPC.EQUITY_REQUEST, async (_event, params) => {
    if (!solver?.initialized) {
      return { error: "Solver not ready" };
    }
    try {
      return solver.equity(params);
    } catch (err) {
      return { error: err.message };
    }
  });

  // ── Full GTO Solve (Rust N-API) ───────────────────────────────
  ipcMain.handle(IPC.SOLVE_REQUEST, async (_event, params) => {
    if (!solver?.initialized) {
      return { error: "Solver not ready" };
    }
    try {
      return solver.solve(params);
    } catch (err) {
      return { error: err.message };
    }
  });

  // ── Batch Equity (multi-table) ────────────────────────────────
  ipcMain.handle(IPC.BATCH_EQUITY, async (_event, requests) => {
    if (!solver?.initialized) {
      return { error: "Solver not ready" };
    }
    try {
      return solver.batchEquity(requests);
    } catch (err) {
      return { error: err.message };
    }
  });

  // ── ADB Tap ───────────────────────────────────────────────────
  ipcMain.handle(
    IPC.ADB_TAP,
    async (_event, { bbox, difficulty, x, y, ghost }) => {
      if (!adb?.connected) {
        return { error: "ADB not connected" };
      }
      try {
        // v2: Full pipeline with cognitive delay + mutex + Gaussian tap
        if (bbox) {
          return await adb.executeAction(bbox, difficulty || "medium");
        }
        // Legacy: raw coordinate tap (dev/debug only)
        const result = ghost ? await adb.ghostTap(x, y) : await adb.tap(x, y);
        return result;
      } catch (err) {
        return { error: err.message };
      }
    },
  );

  // ── Decision Engine (GTO + exploitative overlay) ──────────────
  ipcMain.handle(IPC.DECISION_MADE, async (_event, gameState) => {
    return GtoEngine.decide(gameState);
  });

  // ── Opponent Profiling (variant-isolated) ─────────────────────
  ipcMain.handle(
    IPC.OPPONENT_QUERY,
    async (_event, { action, playerId, variant, data }) => {
      if (!opponentDb) return { error: "DB not ready" };

      switch (action) {
        case "get":
          return opponentDb.getProfile(playerId, variant || "PLO5");
        case "process":
          opponentDb.processHand(data);
          return { ok: true };
        case "list":
          return opponentDb.listAll(variant || "PLO5");
        default:
          return { error: `Unknown action: ${action}` };
      }
    },
  );

  // ── Vision: Detections from inference window ──────────────────
  ipcMain.on(IPC.VISION_DETECTIONS, (_event, payload) => {
    lastVisionResult = payload;

    const detections = payload.detections || [];
    const cardCount = detections.filter((d) => d.classId <= 51).length;
    const buttonCount = detections.filter((d) => d.classId >= 52 && d.classId <= 61).length;
    const { inferenceMs, frameId: fid } = payload;
    log.debug(
      `[Vision] Frame #${fid}: ${cardCount} cards, ${buttonCount} buttons (${inferenceMs}ms)`,
    );

    // Forward detection summary to dashboard
    mainWindow?.webContents.send(IPC.VISION_DETECTIONS, payload);

    // ── Feed into Game Loop (the SOLE consumer of vision data) ──
    if (gameLoop?.running) {
      gameLoop.onVisionFrame(payload);
    }
  });

  // ── Vision: Status from inference window ──────────────────────
  ipcMain.on(IPC.VISION_STATUS, async (_event, status) => {
    log.info(
      `[Vision] Engine status: ready=${status.ready} backend=${status.backend} classes=${status.modelClasses}`,
    );
    mainWindow?.webContents.send(IPC.VISION_STATUS, status);

    // ── Auto-start vision when YOLO is ready ──────────────────
    if (!status.ready) return;

    try {
      const sources = await desktopCapturer.getSources({
        types: ["window"],
        thumbnailSize: { width: 0, height: 0 },
      });

      const ldSource = sources.find((s) =>
        s.name.toLowerCase().includes("ldplayer"),
      );

      if (ldSource && inferenceWindow && !inferenceWindow.isDestroyed()) {
        activeLdSourceId = ldSource.id;
        log.info(
          `[Vision] Found LDPlayer window: "${ldSource.name}" (${ldSource.id})`,
        );
        inferenceWindow.webContents.send(IPC.VISION_START, {
          sourceId: ldSource.id,
          fps: 5,
        });
        log.info(`[Vision] Capture started @ 5 FPS`);

        // Auto-start GameLoop now that vision is flowing
        if (gameLoop && !gameLoop.running && adb?.connected) {
          gameLoop.start();
          log.info(`[GameLoop] Auto-started (LIVE mode — WAITING @ 5 FPS)`);
        }
      } else {
        const names = sources.map((s) => s.name).slice(0, 10);
        log.warn(
          `[Vision] LDPlayer window not found. Available: ${names.join(", ")}`,
        );
      }
    } catch (err) {
      log.error(`[Vision] Auto-start failed: ${err.message}`);
    }
  });

  // ── Vision: Error from inference window ───────────────────────
  ipcMain.on(IPC.VISION_ERROR, (_event, err) => {
    log.error(`[Vision] Error: ${err.error} (fatal=${err.fatal})`);
    if (err.fatal) {
      restartInferenceWindow();
    }
  });

  // ── Vision: Start/Stop controls (dashboard → main → inference) ─
  ipcMain.handle(IPC.VISION_START, async (_event, { sourceId, fps }) => {
    if (!inferenceWindow || inferenceWindow.isDestroyed()) {
      return { error: "Inference window not available" };
    }
    // Store sourceId for resumeVision (COOLDOWN → WAITING recovery)
    if (sourceId) activeLdSourceId = sourceId;
    inferenceWindow.webContents.send(IPC.VISION_START, { sourceId, fps });
    return { ok: true };
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

  // ── Health Report ─────────────────────────────────────────────
  ipcMain.handle(IPC.HEALTH_REPORT, async () => {
    const solverStats = solver?.getStats() || {};
    return {
      adb: adb?.connected || false,
      solver: {
        ready: solver?.initialized || false,
        native: solver?.native || false,
        version: solver?.version || "unknown",
        ...solverStats,
      },
      db: !!opponentDb,
      vision: {
        windowAlive: !!inferenceWindow && !inferenceWindow.isDestroyed(),
        lastFrame: lastVisionResult
          ? {
              frameId: lastVisionResult.frameId,
              cards: lastVisionResult.cards?.length || 0,
              inferenceMs: lastVisionResult.inferenceMs,
              backend: lastVisionResult.backend,
            }
          : null,
      },
      emulator: bootstrapResult
        ? {
            bootstrapped: bootstrapResult.success,
            device: bootstrapResult.device,
            resolution: bootstrapResult.after.resolution,
            dpi: bootstrapResult.after.dpi,
            animations: bootstrapResult.after.animations,
            applied: bootstrapResult.applied.length,
            errors: bootstrapResult.errors,
          }
        : null,
      uptime: process.uptime(),
      memory: process.memoryUsage(),
      gameLoop: gameLoop
        ? {
            state: gameLoop.state,
            running: gameLoop.running,
            ...gameLoop.stats,
          }
        : null,
    };
  });

  // ── Solver Stats (diagnostics) ────────────────────────────────
  ipcMain.handle(IPC.SOLVER_STATS, async () => {
    if (!solver) return { error: "Solver not ready" };
    return solver.getStats();
  });

  // ── Game Loop Controls ────────────────────────────────────────
  ipcMain.handle(IPC.GAMELOOP_START, async () => {
    if (!gameLoop) return { error: "Game Loop not initialized" };
    if (!adb?.connected) return { error: "ADB not connected" };
    gameLoop.start();
    return { ok: true, state: gameLoop.state };
  });

  ipcMain.handle(IPC.GAMELOOP_STOP, async () => {
    if (!gameLoop) return { error: "Game Loop not initialized" };
    gameLoop.stop();
    return { ok: true, state: gameLoop.state };
  });

  ipcMain.handle(IPC.GAMELOOP_STATS, async () => {
    if (!gameLoop) return { error: "Game Loop not initialized" };
    return gameLoop.stats;
  });
}

// ── Logging ─────────────────────────────────────────────────────────

function logSystemSummary() {
  const native = solver?.native ? "RUST N-API" : "JS FALLBACK (SLOW)";
  const solverVersion = solver?.version || "unknown";
  const adbOk = adb?.connected ? "Connected" : "Offline";
  const dbOk = opponentDb ? "Ready" : "Failed";
  const inferOk =
    inferenceWindow && !inferenceWindow.isDestroyed() ? "Ready" : "Failed";
  const mode = "LIVE";

  log.info("╔══════════════════════════════════════════════════╗");
  log.info("║          TITAN EDGE AI v3 — SYSTEM STATUS       ║");
  log.info("╠══════════════════════════════════════════════════╣");
  log.info(`║  Mode:       ${mode.padEnd(35)}║`);
  log.info(`║  Solver:     ${native.padEnd(35)}║`);
  log.info(`║  Version:    ${solverVersion.padEnd(35)}║`);
  log.info(`║  ADB:        ${adbOk.padEnd(35)}║`);
  log.info(`║  Opponent DB: ${dbOk.padEnd(34)}║`);
  log.info(`║  Inference:  ${inferOk.padEnd(35)}║`);
  log.info("╠══════════════════════════════════════════════════╣");
  log.info("║  Equity Target: <3ms (Rust) vs 173ms (old JS)  ║");
  log.info("║  Vision Target: <35ms (WebGPU YOLO)             ║");
  log.info("║  Variants: PLO5 (5 cards) + PLO6 (6 cards)     ║");
  log.info("╚══════════════════════════════════════════════════╝");
}

// ── Shutdown ────────────────────────────────────────────────────────

async function shutdown() {
  try {
    if (gameLoop) gameLoop.stop();
    if (solver) await solver.shutdown();
    if (opponentDb) opponentDb.close();
    if (inferenceWindow && !inferenceWindow.isDestroyed()) {
      inferenceWindow.destroy();
    }
  } catch (err) {
    log.error(`[Titan] Shutdown error: ${err.message}`);
  }
}

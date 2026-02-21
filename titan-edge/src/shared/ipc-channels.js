/**
 * IPC Channel Registry — Single source of truth for all inter-process
 * communication channels between main ↔ renderer(s).
 *
 * Convention: DOMAIN:ACTION (kebab-case)
 *
 * Windows:
 *   - Dashboard (mainWindow): visible UI, stats, HUD
 *   - Inference (inferenceWindow): hidden, WebGPU YOLO engine
 */

const IPC = Object.freeze({
  // ── Vision (inference renderer → main → dashboard) ─────────────────
  VISION_FRAME_READY: "vision:frame-ready",
  VISION_DETECTIONS: "vision:detections",
  VISION_STATUS: "vision:status",
  VISION_ERROR: "vision:error",
  VISION_START: "vision:start",
  VISION_STOP: "vision:stop",
  VISION_CONFIG: "vision:config",

  // ── Sources ─────────────────────────────────────────────────────────
  GET_SOURCES: "get-sources",

  // ── Solver / Brain (main ↔ renderer) ───────────────────────────────
  EQUITY_REQUEST: "brain:equity-request",
  EQUITY_RESULT: "brain:equity-result",
  SOLVE_REQUEST: "brain:solve-request",
  BATCH_EQUITY: "brain:batch-equity",
  DECISION_MADE: "brain:decision-made",
  SOLVER_STATS: "brain:solver-stats",

  // ── Execution (main) ──────────────────────────────────────────────
  ADB_STATUS: "exec:adb-status",
  ADB_TAP: "exec:adb-tap",
  ADB_TAP_RESULT: "exec:adb-tap-result",

  // ── Profiling ─────────────────────────────────────────────────────
  OPPONENT_QUERY: "profile:opponent-query",
  OPPONENT_RESULT: "profile:opponent-result",

  // ── Orchestrator ──────────────────────────────────────────────────
  ENGINE_TICK: "engine:tick",
  ENGINE_STATE: "engine:state",
  HEALTH_REPORT: "engine:health-report",

  // ── Config ────────────────────────────────────────────────────────
  CONFIG_LOAD: "config:load",
  CONFIG_UPDATE: "config:update",
});

module.exports = IPC;

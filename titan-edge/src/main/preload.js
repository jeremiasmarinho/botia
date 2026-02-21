/**
 * Preload Script — Dashboard Context Bridge
 *
 * Exposes a safe API surface to the dashboard renderer via contextBridge.
 * All main process functionality is accessed through titanAPI.
 *
 * Security: contextIsolation = true, nodeIntegration = false.
 * The renderer CANNOT access Node.js, only the exposed API.
 */

"use strict";

const { contextBridge, ipcRenderer } = require("electron");
const IPC = require("../shared/ipc-channels");

contextBridge.exposeInMainWorld("titanAPI", {
  // ── Vision Control ──────────────────────────────────────────────
  getSources: () => ipcRenderer.invoke(IPC.GET_SOURCES),
  startVision: (sourceId, fps) =>
    ipcRenderer.invoke(IPC.VISION_START, { sourceId, fps }),
  stopVision: () => ipcRenderer.invoke(IPC.VISION_STOP),
  configVision: (config) => ipcRenderer.invoke(IPC.VISION_CONFIG, config),

  // ── Solver (Rust N-API) ─────────────────────────────────────────
  calculateEquity: (params) => ipcRenderer.invoke(IPC.EQUITY_REQUEST, params),
  solve: (params) => ipcRenderer.invoke(IPC.SOLVE_REQUEST, params),
  batchEquity: (requests) => ipcRenderer.invoke(IPC.BATCH_EQUITY, requests),
  getSolverStats: () => ipcRenderer.invoke(IPC.SOLVER_STATS),
  getDecision: (gameState) => ipcRenderer.invoke(IPC.DECISION_MADE, gameState),

  // ── Execution ───────────────────────────────────────────────────
  tap: (x, y, ghost = true) => ipcRenderer.invoke(IPC.ADB_TAP, { x, y, ghost }),

  // ── Profiling (variant-isolated) ────────────────────────────────
  getOpponent: (playerId, variant) =>
    ipcRenderer.invoke(IPC.OPPONENT_QUERY, {
      action: "get",
      playerId,
      variant,
    }),
  processHand: (data) =>
    ipcRenderer.invoke(IPC.OPPONENT_QUERY, { action: "process", data }),
  listOpponents: (variant) =>
    ipcRenderer.invoke(IPC.OPPONENT_QUERY, { action: "list", variant }),

  // ── System ──────────────────────────────────────────────────────
  getHealth: () => ipcRenderer.invoke(IPC.HEALTH_REPORT),

  // ── Events (main → renderer) ────────────────────────────────────
  onAdbStatus: (callback) =>
    ipcRenderer.on(IPC.ADB_STATUS, (_e, data) => callback(data)),
  onEngineState: (callback) =>
    ipcRenderer.on(IPC.ENGINE_STATE, (_e, data) => callback(data)),
  onVisionDetections: (callback) =>
    ipcRenderer.on(IPC.VISION_DETECTIONS, (_e, data) => callback(data)),
  onVisionStatus: (callback) =>
    ipcRenderer.on(IPC.VISION_STATUS, (_e, data) => callback(data)),
});

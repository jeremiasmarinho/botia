/**
 * Preload Script — Inference Window Context Bridge
 *
 * Exposes a minimal IPC surface for the hidden inference window.
 * This window only sends detections/status and receives start/stop commands.
 *
 * Security: contextIsolation = true, nodeIntegration = false.
 */

"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// IPC channel names (inlined to avoid require issues in sandboxed preload)
const IPC = {
  VISION_DETECTIONS: "vision:detections",
  VISION_STATUS: "vision:status",
  VISION_ERROR: "vision:error",
  VISION_START: "vision:start",
  VISION_STOP: "vision:stop",
  VISION_CONFIG: "vision:config",
};

contextBridge.exposeInMainWorld("inferenceAPI", {
  // ── Outbound (inference → main) ─────────────────────────────────
  sendDetections: (payload) => ipcRenderer.send(IPC.VISION_DETECTIONS, payload),
  sendStatus: (status) => ipcRenderer.send(IPC.VISION_STATUS, status),
  sendError: (err) => ipcRenderer.send(IPC.VISION_ERROR, err),
  sendDebugFrame: (payload) =>
    ipcRenderer.send("vision:save-debug-frame", payload),

  // ── Inbound (main → inference) ──────────────────────────────────
  onStart: (callback) =>
    ipcRenderer.on(IPC.VISION_START, (_e, data) => callback(data)),
  onStop: (callback) => ipcRenderer.on(IPC.VISION_STOP, () => callback()),
  onConfig: (callback) =>
    ipcRenderer.on(IPC.VISION_CONFIG, (_e, config) => callback(config)),
  onAdbFrame: (callback) =>
    ipcRenderer.on("vision:adb-frame", (_e, data) => callback(data)),
});

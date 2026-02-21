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
const IPC = require("../shared/ipc-channels");

contextBridge.exposeInMainWorld("inferenceAPI", {
  // ── Outbound (inference → main) ─────────────────────────────────
  sendDetections: (payload) => ipcRenderer.send(IPC.VISION_DETECTIONS, payload),
  sendStatus: (status) => ipcRenderer.send(IPC.VISION_STATUS, status),
  sendError: (err) => ipcRenderer.send(IPC.VISION_ERROR, err),

  // ── Inbound (main → inference) ──────────────────────────────────
  onStart: (callback) =>
    ipcRenderer.on(IPC.VISION_START, (_e, data) => callback(data)),
  onStop: (callback) => ipcRenderer.on(IPC.VISION_STOP, () => callback()),
  onConfig: (callback) =>
    ipcRenderer.on(IPC.VISION_CONFIG, (_e, config) => callback(config)),
});

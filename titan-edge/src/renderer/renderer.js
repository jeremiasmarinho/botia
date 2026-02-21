/**
 * Renderer Process — Dashboard UI + Vision Pipeline Orchestration
 *
 * This runs inside Chromium with access to WebGPU.
 * All Node.js functionality is accessed through the titanAPI context bridge.
 */

"use strict";

// ── DOM References ──────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

const ui = {
  adbStatus: $("adb-status"),
  equityStatus: $("equity-status"),
  visionStatus: $("vision-status"),
  heroCards: $("hero-cards"),
  boardCards: $("board-cards"),
  equityFill: $("equity-fill"),
  equityValue: $("equity-value"),
  decisionAction: $("decision-action"),
  decisionReason: $("decision-reasoning"),
  inferenceMs: $("inference-ms"),
  detectionCount: $("detection-count"),
  uptime: $("uptime"),
  memory: $("memory"),
  workers: $("workers"),
  opponentList: $("opponent-list"),
  yoloCanvas: $("yolo-canvas"),
};

// ── Event Listeners (Main → Renderer) ───────────────────────────────

window.titanAPI.onAdbStatus((data) => {
  if (data.connected) {
    ui.adbStatus.textContent = `ADB: ${data.device}`;
    ui.adbStatus.className = "badge badge--online";
  } else {
    ui.adbStatus.textContent = "ADB: Offline";
    ui.adbStatus.className = "badge badge--offline";
  }
});

// ── Health Monitor ──────────────────────────────────────────────────

async function updateHealth() {
  try {
    const health = await window.titanAPI.getHealth();

    ui.uptime.textContent = `${Math.round(health.uptime)}s`;
    ui.memory.textContent = `${Math.round(health.memory.heapUsed / 1024 / 1024)}MB`;

    ui.equityStatus.textContent = health.equity
      ? "Equity: Ready"
      : "Equity: --";
    ui.equityStatus.className = `badge badge--${health.equity ? "online" : "offline"}`;
  } catch {
    // Silently retry
  }
}

setInterval(updateHealth, 3000);
updateHealth();

// ── Equity Demo ─────────────────────────────────────────────────────

async function testEquity() {
  const result = await window.titanAPI.calculateEquity({
    hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
    board: ["2c", "7d", "9s"],
    sims: 3000,
    opponents: 1,
  });

  if (result.equity !== undefined) {
    const pct = Math.round(result.equity * 100);
    ui.equityFill.style.width = `${pct}%`;
    ui.equityValue.textContent = `${pct}%`;
  }
}

// ── Card Display ────────────────────────────────────────────────────

function renderCards(containerId, cards) {
  const container = $(containerId);
  container.innerHTML = "";

  for (const card of cards) {
    const slot = document.createElement("div");
    slot.className = `card-slot ${card !== "?" ? "detected" : ""}`;
    slot.textContent = card;
    container.appendChild(slot);
  }
}

// ── Opponent Display ────────────────────────────────────────────────

async function loadOpponents() {
  try {
    const opponents = await window.titanAPI.listOpponents();
    if (!opponents || opponents.length === 0) return;

    ui.opponentList.innerHTML = "";
    for (const opp of opponents.slice(0, 10)) {
      const card = document.createElement("div");
      card.className = "opponent-card";
      card.innerHTML = `
        <div class="name">${opp.alias || opp.player_id}</div>
        <div class="stats">
          VPIP: ${(opp.vpip * 100).toFixed(0)}% |
          PFR: ${(opp.pfr * 100).toFixed(0)}% |
          AF: ${opp.af.toFixed(1)} |
          Hands: ${opp.hands}
        </div>
      `;
      ui.opponentList.appendChild(card);
    }
  } catch {
    // DB not ready yet
  }
}

setInterval(loadOpponents, 10000);

// ── Vision Pipeline Placeholder ─────────────────────────────────────
// The full YOLO inference pipeline will be implemented in
// vision/yolo-inference.js using TF.js + WebGPU backend.
// For now, we show the canvas placeholder.

console.log("[Renderer] Titan Edge AI dashboard loaded.");
console.log("[Renderer] WebGPU available:", !!navigator.gpu);

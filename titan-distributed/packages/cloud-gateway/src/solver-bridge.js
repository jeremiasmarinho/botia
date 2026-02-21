/**
 * Titan Cloud Gateway — Solver Bridge (N-API ↔ Rust)
 *
 * This module is the critical junction between Node.js and the Rust
 * core-engine compiled as a native .node addon via N-API (neon).
 *
 * Architecture:
 *   solver-bridge.js  →  require('titan-core-engine')  →  Rust .node binary
 *                              ↓
 *                         Deep CFR Strategy Lookup
 *                         Omaha Equity Evaluator
 *                         Hand Abstraction Tables
 *
 * The Rust addon exposes these N-API functions:
 *   - solve(params: Buffer) → Buffer     (protobuf in/out for zero-copy)
 *   - evaluate(cards: Uint8Array) → number  (raw 5-card evaluator)
 *   - equity(hero: Uint8Array, board: Uint8Array, sims: number) → number
 *   - version() → string
 *
 * Performance Target: <1ms per Solve call (with pre-loaded CFR tables).
 */

"use strict";

const path = require("node:path");
const { createLogger } = require("./logger");

const log = createLogger("solver-bridge");

// ── Native Addon Loading ────────────────────────────────────────────

/**
 * Attempts to load the Rust native addon from multiple paths.
 * Falls back to a JS-only solver for development/CI environments.
 */
function loadNativeAddon() {
  const searchPaths = [
    // Release build (production)
    path.resolve(__dirname, "../../core-engine/native/titan_core.node"),
    // Debug build (development)
    path.resolve(__dirname, "../../core-engine/target/release/titan_core.node"),
    path.resolve(__dirname, "../../core-engine/target/debug/titan_core.node"),
    // npm-installed pre-built binary
    "titan-core-engine",
  ];

  for (const p of searchPaths) {
    try {
      const addon = require(p);
      if (typeof addon.solve === "function") {
        log.info({ path: p }, "Rust native addon loaded");
        return addon;
      }
    } catch {
      // continue searching
    }
  }

  log.warn("Rust native addon not found — using JS fallback solver");
  return null;
}

// ── JS Fallback Solver ──────────────────────────────────────────────

/**
 * Pure JS fallback when Rust engine is unavailable.
 * Implements a simplified Monte Carlo solver for development/testing.
 */
class JsFallbackSolver {
  constructor() {
    this.version = "js-fallback-0.1.0";
  }

  solve(params) {
    const { heroCards, boardCards, street, potBb100, heroStack } = params;
    const handSize = heroCards?.length || 5;

    // Simplified equity via random sampling
    const equity = this._monteCarloEquity(
      heroCards,
      boardCards,
      2000,
      handSize,
    );
    const spr = heroStack / Math.max(potBb100, 1);

    // Basic strategy selection
    let action, raiseAmount;
    if (equity > 0.7) {
      action = spr < 2 ? "ACTION_ALLIN" : "ACTION_RAISE";
      raiseAmount = Math.round(potBb100 * 0.75);
    } else if (equity > 0.5) {
      action = "ACTION_CALL";
      raiseAmount = 0;
    } else if (equity > 0.3) {
      action = "ACTION_CHECK";
      raiseAmount = 0;
    } else {
      action = "ACTION_FOLD";
      raiseAmount = 0;
    }

    return {
      action,
      raiseAmountBb100: raiseAmount,
      equity,
      evBb100: Math.round(
        equity * potBb100 - (1 - equity) * (raiseAmount || potBb100 * 0.5),
      ),
      frequencies: {
        fold: equity < 0.3 ? 0.8 : 0.0,
        check: equity >= 0.3 && equity < 0.5 ? 0.7 : 0.0,
        call: equity >= 0.5 && equity < 0.7 ? 0.8 : 0.0,
        raise: equity >= 0.7 ? 0.9 : 0.0,
        allin: spr < 2 && equity > 0.7 ? 0.8 : 0.0,
      },
      confidence: 0.4, // Low confidence for fallback solver
      solverId: "js-fallback",
    };
  }

  /**
   * Simplified Monte Carlo — does NOT use proper Omaha C(hand,2)×C(board,3).
   * Only for development. The Rust engine handles this properly.
   */
  _monteCarloEquity(heroCards, boardCards, sims, handSize) {
    if (!heroCards || heroCards.length === 0) return 0.5;

    // Use card values to estimate relative strength
    const heroStrength =
      heroCards.reduce((sum, c) => sum + (c >> 2), 0) / heroCards.length;
    const normalized = heroStrength / 12; // 0..1 range

    // Add some variance based on board texture
    const boardBonus = boardCards ? boardCards.length * 0.02 : 0;

    return Math.min(0.95, Math.max(0.05, normalized * 0.6 + 0.2 + boardBonus));
  }
}

// ── Solver Bridge Class ─────────────────────────────────────────────

class SolverBridge {
  constructor() {
    this._native = null;
    this._fallback = new JsFallbackSolver();
    this._initialized = false;
    this.version = "unknown";
  }

  /**
   * Initialize the bridge. Loads Rust addon or falls back to JS.
   */
  async init() {
    this._native = loadNativeAddon();

    if (this._native) {
      // Initialize Rust engine (loads CFR strategy tables, etc.)
      if (typeof this._native.init === "function") {
        log.info("Initializing Rust engine (loading CFR tables)...");
        const t0 = performance.now();
        await this._native.init();
        const elapsed = (performance.now() - t0).toFixed(0);
        log.info({ elapsedMs: elapsed }, "Rust engine initialized");
      }
      this.version = this._native.version?.() || "rust-unknown";
    } else {
      this.version = this._fallback.version;
    }

    this._initialized = true;
    log.info(
      { version: this.version, native: !!this._native },
      "SolverBridge ready",
    );
  }

  /**
   * Solve a game state. Returns the optimal action with frequencies.
   *
   * @param {object} params - Solver parameters
   * @param {string} params.format - 'FORMAT_PLO5' or 'FORMAT_PLO6'
   * @param {string} params.street - 'STREET_PREFLOP' .. 'STREET_RIVER'
   * @param {number[]} params.heroCards - Card IDs (0-51)
   * @param {number[]} params.boardCards - Board card IDs
   * @param {number[]} params.deadCards - Known dead cards
   * @param {number} params.potBb100 - Pot size in BB×100
   * @param {number} params.heroStack - Hero stack in BB×100
   * @param {number[]} params.villainStacks - Villain stacks
   * @param {object[]} params.actions - Action history
   * @param {string} params.position - Hero position
   * @param {number} params.numPlayers - Players at table
   *
   * @returns {{ action: string, raiseAmountBb100: number, equity: number,
   *             evBb100: number, frequencies: object, confidence: number }}
   */
  solve(params) {
    if (!this._initialized) {
      throw new Error("SolverBridge not initialized. Call init() first.");
    }

    if (this._native) {
      return this._nativeSolve(params);
    }

    return this._fallback.solve(params);
  }

  /**
   * Partial solve for streaming — returns intermediate equity estimate.
   */
  partialSolve(state, progress) {
    if (this._native && typeof this._native.partialSolve === "function") {
      return this._native.partialSolve(state, progress);
    }
    // Fallback: just compute full equity (fast enough in JS)
    const result = this._fallback.solve(state);
    return result.equity * progress + 0.5 * (1 - progress);
  }

  /**
   * Route to native Rust engine.
   * Uses direct object passing (NAPI-RS supports this via serde).
   */
  _nativeSolve(params) {
    const t0 = performance.now();

    // For maximum performance, the Rust addon accepts a flat object
    // and returns a flat object. NAPI-RS handles the V8 ↔ Rust
    // marshalling automatically via serde + napi derive macros.
    const result = this._native.solve({
      format: formatToInt(params.format),
      street: streetToInt(params.street),
      hero_cards: new Uint8Array(params.heroCards || []),
      board_cards: new Uint8Array(params.boardCards || []),
      dead_cards: new Uint8Array(params.deadCards || []),
      pot_bb100: params.potBb100 || 0,
      hero_stack: params.heroStack || 0,
      villain_stacks: new Uint32Array(params.villainStacks || []),
      position: positionToInt(params.position),
      num_players: params.numPlayers || 2,
    });

    const solveUs = Math.round((performance.now() - t0) * 1000);

    if (solveUs > 1000) {
      log.warn({ solveUs }, "Solve exceeded 1ms target");
    }

    return {
      action: intToAction(result.action),
      raiseAmountBb100: result.raise_amount_bb100,
      equity: result.equity,
      evBb100: result.ev_bb100,
      frequencies: {
        fold: result.freq_fold,
        check: result.freq_check,
        call: result.freq_call,
        raise: result.freq_raise,
        allin: result.freq_allin,
      },
      confidence: result.confidence,
      solverId: "rust-cfr",
    };
  }
}

// ── Enum Conversion Helpers ─────────────────────────────────────────

function formatToInt(s) {
  const map = { FORMAT_PLO5: 0, FORMAT_PLO6: 1, FORMAT_NLH: 2 };
  return map[s] ?? 0;
}

function streetToInt(s) {
  const map = {
    STREET_PREFLOP: 0,
    STREET_FLOP: 1,
    STREET_TURN: 2,
    STREET_RIVER: 3,
  };
  return map[s] ?? 0;
}

function positionToInt(s) {
  const map = {
    POS_BTN: 0,
    POS_SB: 1,
    POS_BB: 2,
    POS_UTG: 3,
    POS_MP: 4,
    POS_CO: 5,
  };
  return map[s] ?? 3;
}

function intToAction(n) {
  const map = [
    "ACTION_FOLD",
    "ACTION_CHECK",
    "ACTION_CALL",
    "ACTION_RAISE",
    "ACTION_ALLIN",
  ];
  return map[n] ?? "ACTION_FOLD";
}

module.exports = { SolverBridge };

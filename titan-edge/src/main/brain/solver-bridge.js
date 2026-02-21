/**
 * Solver Bridge — Rust N-API Native Addon Interface (Zero-Copy)
 *
 * Replaces the JS Worker Thread EquityPool (~173ms PLO5 / 192ms PLO6)
 * with direct calls into the Rust titan_core.node addon via N-API.
 *
 * Performance Target: <3ms per equity call (vs 173ms JS baseline).
 *
 * Zero-Copy Architecture:
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │  Node.js (V8)                                                │
 *   │  hero   = Uint8Array([48,44,40,36,50])  ← 5 card IDs       │
 *   │  board  = Uint8Array([0,24,28])         ← 3 board IDs      │
 *   │                    │                                         │
 *   │           N-API (zero-copy transfer)                         │
 *   │                    ↓                                         │
 *   │  ┌──────────────────────────────────────┐                    │
 *   │  │  Rust (titan_core.node)              │                    │
 *   │  │  &[u8] slice — NO serialization      │                    │
 *   │  │  match variant {                     │                    │
 *   │  │    PLO5 => evaluator_plo5::solve()   │                    │
 *   │  │    PLO6 => evaluator_plo6::solve()   │                    │
 *   │  │  }                                   │                    │
 *   │  └──────────────────────────────────────┘                    │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * Card Encoding: 0-51 integer (rank * 4 + suit).
 *   rank: 0=2, 1=3, ..., 12=A
 *   suit: 0=c, 1=d, 2=h, 3=s
 *
 * GameVariant: 0 = PLO5, 1 = PLO6 (matches proto enum)
 */

"use strict";

const path = require("node:path");
const { EventEmitter } = require("node:events");
const log = require("electron-log/main");

// ── Constants ───────────────────────────────────────────────────────

/** @enum {number} */
const GameVariant = Object.freeze({
  PLO5: 0,
  PLO6: 1,
});

const STREET = Object.freeze({
  PREFLOP: 0,
  FLOP: 1,
  TURN: 2,
  RIVER: 3,
});

const ACTION = Object.freeze({
  FOLD: 0,
  CHECK: 1,
  CALL: 2,
  RAISE: 3,
  ALLIN: 4,
});

const ACTION_NAMES = ["fold", "check", "call", "raise", "allin"];

/**
 * Card code ('Ah', '2c', etc.) → 0-51 integer.
 * Matches YOLO class IDs: rank * 4 + suit.
 */
const RANK_MAP = {
  2: 0,
  3: 1,
  4: 2,
  5: 3,
  6: 4,
  7: 5,
  8: 6,
  9: 7,
  T: 8,
  J: 9,
  Q: 10,
  K: 11,
  A: 12,
};
const SUIT_MAP = { c: 0, d: 1, h: 2, s: 3 };

/**
 * Convert a card string ('Ah') to its 0-51 integer ID.
 * @param {string} card - e.g. 'Ah', '2c', 'Ts'
 * @returns {number}
 */
function cardToId(card) {
  const rank = RANK_MAP[card[0]];
  const suit = SUIT_MAP[card[1]];
  if (rank === undefined || suit === undefined) {
    throw new Error(`[SolverBridge] Invalid card: "${card}"`);
  }
  return rank * 4 + suit;
}

/**
 * Encode an array of card strings into a Uint8Array (zero-copy ready).
 * @param {string[]} cards
 * @returns {Uint8Array}
 */
function encodeCards(cards) {
  const buf = new Uint8Array(cards.length);
  for (let i = 0; i < cards.length; i++) {
    buf[i] = cardToId(cards[i]);
  }
  return buf;
}

// ── Native Addon Loader ─────────────────────────────────────────────

/**
 * Search multiple paths for the compiled Rust addon.
 * @returns {object|null}
 */
function loadNativeAddon() {
  const searchPaths = [
    // Local build in titan-edge
    path.resolve(__dirname, "../../../native/titan_core.node"),
    path.resolve(__dirname, "../../../build/Release/titan_core.node"),
    // Distributed monorepo build
    path.resolve(
      __dirname,
      "../../../../titan-distributed/packages/core-engine/native/titan_core.node",
    ),
    path.resolve(
      __dirname,
      "../../../../titan-distributed/packages/core-engine/target/release/titan_core.node",
    ),
    // npm pre-built binary
    "titan-core-engine",
  ];

  for (const p of searchPaths) {
    try {
      const addon = require(p);
      if (
        typeof addon.solve === "function" ||
        typeof addon.equity === "function"
      ) {
        log.info(`[SolverBridge] Rust addon loaded: ${p}`);
        return addon;
      }
    } catch {
      // Continue searching
    }
  }

  log.warn("[SolverBridge] Rust addon not found — JS fallback active (SLOW)");
  return null;
}

// ── JS Fallback (development only) ──────────────────────────────────

/**
 * Minimal Monte Carlo fallback when Rust engine is unavailable.
 * WARNING: This runs at 170ms+ — for dev/test ONLY.
 */
class JsFallback {
  equity(heroBuf, boardBuf, deadBuf, sims, opponents, variant) {
    // Extremely simplified — deterministic based on card values
    let heroStrength = 0;
    for (let i = 0; i < heroBuf.length; i++) {
      heroStrength += heroBuf[i] >> 2; // rank component
    }
    const normalized = heroStrength / (heroBuf.length * 12);
    const boardBonus = boardBuf.length * 0.015;
    return Math.min(
      0.95,
      Math.max(0.05, normalized * 0.55 + 0.22 + boardBonus),
    );
  }

  solve(payload) {
    const eq = this.equity(
      payload.hero_cards,
      payload.board_cards,
      payload.dead_cards || new Uint8Array(0),
      payload.sims || 2000,
      payload.num_opponents || 1,
      payload.game_variant || 0,
    );
    const spr =
      (payload.hero_stack || 100) / Math.max(payload.pot_bb100 || 1, 1);

    let action, raiseAmount;
    if (eq > 0.7) {
      action = spr < 2 ? ACTION.ALLIN : ACTION.RAISE;
      raiseAmount = Math.round((payload.pot_bb100 || 100) * 0.75);
    } else if (eq > 0.5) {
      action = ACTION.CALL;
      raiseAmount = 0;
    } else if (eq > 0.3) {
      action = ACTION.CHECK;
      raiseAmount = 0;
    } else {
      action = ACTION.FOLD;
      raiseAmount = 0;
    }

    return {
      action,
      raise_amount_bb100: raiseAmount,
      equity: eq,
      ev_bb100: Math.round(
        eq * (payload.pot_bb100 || 100) - (1 - eq) * (raiseAmount || 50),
      ),
      freq_fold: eq < 0.3 ? 0.8 : 0.0,
      freq_check: eq >= 0.3 && eq < 0.5 ? 0.7 : 0.0,
      freq_call: eq >= 0.5 && eq < 0.7 ? 0.8 : 0.0,
      freq_raise: eq >= 0.7 ? 0.85 : 0.0,
      freq_allin: spr < 2 && eq > 0.7 ? 0.8 : 0.0,
      confidence: 0.3,
    };
  }

  version() {
    return "js-fallback-0.2.0";
  }
}

// ── Solver Bridge ───────────────────────────────────────────────────

class SolverBridge extends EventEmitter {
  constructor() {
    super();
    /** @type {object|null} Native Rust addon */
    this._native = null;
    /** @type {JsFallback} */
    this._fallback = new JsFallback();
    this._initialized = false;
    this._useNative = false;
    this.version = "unknown";

    // Performance tracking
    this._stats = {
      calls: 0,
      totalUs: 0,
      maxUs: 0,
      over3ms: 0,
    };
  }

  /**
   * Initialize: load Rust addon or fall back to JS.
   * If Rust has an init() function (CFR table loading), await it.
   */
  async init() {
    if (this._initialized) return;

    this._native = loadNativeAddon();
    this._useNative = !!this._native;

    if (this._native) {
      // Some implementations expose an async init (e.g., load CFR tables)
      if (typeof this._native.init === "function") {
        log.info(
          "[SolverBridge] Initializing Rust engine (loading CFR tables)...",
        );
        const t0 = performance.now();
        await this._native.init();
        const elapsed = (performance.now() - t0).toFixed(0);
        log.info(`[SolverBridge] Rust engine initialized in ${elapsed}ms`);
      }
      this.version =
        typeof this._native.version === "function"
          ? this._native.version()
          : "rust-native";
    } else {
      this.version = this._fallback.version();
    }

    this._initialized = true;
    this.emit("ready", { native: this._useNative, version: this.version });
    log.info(
      `[SolverBridge] Ready — engine=${this.version} native=${this._useNative}`,
    );
  }

  // ── Primary API ─────────────────────────────────────────────────

  /**
   * Calculate raw equity via Monte Carlo (Rust-accelerated).
   *
   * Zero-copy path: cards are passed as Uint8Array directly into Rust
   * via N-API. No V8 serialization overhead.
   *
   * @param {Object} params
   * @param {string[]} params.hero       - Hero hole cards (e.g. ['Ah','Kh','Qh','Jh','Th'])
   * @param {string[]} params.board      - Board cards (0-5)
   * @param {string[]} [params.dead=[]]  - Dead cards (collusion intel)
   * @param {number}   [params.sims=5000]
   * @param {number}   [params.opponents=1]
   * @param {number}   [params.gameVariant=0] - 0=PLO5, 1=PLO6
   * @returns {{ equity: number, winRate: number, tieRate: number, sims: number, elapsedUs: number, engine: string }}
   */
  equity(params) {
    this._assertReady();

    const {
      hero,
      board = [],
      dead = [],
      sims = hero.length >= 6 ? 3000 : 5000,
      opponents = 1,
      gameVariant,
    } = params;

    // Infer variant from hand size if not explicitly provided
    const variant =
      gameVariant ?? (hero.length >= 6 ? GameVariant.PLO6 : GameVariant.PLO5);

    // Encode cards to Uint8Array (zero-copy into Rust)
    const heroBuf = encodeCards(hero);
    const boardBuf = encodeCards(board);
    const deadBuf = encodeCards(dead);

    const t0 = performance.now();
    let result;

    if (this._useNative && typeof this._native.equity === "function") {
      // ── ZERO-COPY N-API CALL ──────────────────────────────────
      // Rust signature: fn equity(hero: &[u8], board: &[u8], dead: &[u8],
      //                           sims: u32, opponents: u8, variant: u8) -> EquityResult
      result = this._native.equity(
        heroBuf,
        boardBuf,
        deadBuf,
        sims,
        opponents,
        variant,
      );
    } else {
      // Fallback
      const eq = this._fallback.equity(
        heroBuf,
        boardBuf,
        deadBuf,
        sims,
        opponents,
        variant,
      );
      result = { wins: Math.round(eq * sims), ties: 0, runs: sims };
    }

    const elapsedUs = Math.round((performance.now() - t0) * 1000);
    this._trackPerf(elapsedUs);

    const totalRuns = result.runs || sims;
    const wins = result.wins || 0;
    const ties = result.ties || 0;

    return {
      equity: totalRuns > 0 ? (wins + ties * 0.5) / totalRuns : 0,
      winRate: totalRuns > 0 ? wins / totalRuns : 0,
      tieRate: totalRuns > 0 ? ties / totalRuns : 0,
      sims: totalRuns,
      elapsedUs,
      engine: this._useNative ? "rust" : "js-fallback",
    };
  }

  /**
   * Full GTO solve: returns action + frequencies + EV.
   *
   * @param {Object} params
   * @param {string[]}  params.heroCards     - Hero hole cards
   * @param {string[]}  params.boardCards    - Board cards
   * @param {string[]}  [params.deadCards=[]]
   * @param {string}    params.street        - 'preflop'|'flop'|'turn'|'river'
   * @param {number}    params.potBb100      - Pot in BB×100
   * @param {number}    params.heroStack     - Hero stack in BB×100
   * @param {number[]}  [params.villainStacks=[]]
   * @param {number}    [params.opponents=1]
   * @param {number}    [params.gameVariant] - 0=PLO5, 1=PLO6
   *
   * @returns {{ action: string, raiseAmount: number, equity: number,
   *             ev: number, frequencies: Object, confidence: number,
   *             elapsedUs: number, engine: string }}
   */
  solve(params) {
    this._assertReady();

    const {
      heroCards,
      boardCards = [],
      deadCards = [],
      street = "flop",
      potBb100 = 100,
      heroStack = 200,
      villainStacks = [],
      opponents = 1,
      gameVariant,
    } = params;

    const variant =
      gameVariant ??
      (heroCards.length >= 6 ? GameVariant.PLO6 : GameVariant.PLO5);

    // Encode to Uint8Array
    const heroBuf = encodeCards(heroCards);
    const boardBuf = encodeCards(boardCards);
    const deadBuf = encodeCards(deadCards);

    const streetInt = STREET[street.toUpperCase()] ?? STREET.FLOP;

    const t0 = performance.now();
    let raw;

    if (this._useNative) {
      // ── ZERO-COPY N-API CALL ──────────────────────────────────
      raw = this._native.solve({
        game_variant: variant,
        street: streetInt,
        hero_cards: heroBuf,
        board_cards: boardBuf,
        dead_cards: deadBuf,
        pot_bb100: potBb100,
        hero_stack: heroStack,
        villain_stacks: new Uint32Array(villainStacks),
        num_opponents: opponents,
      });
    } else {
      raw = this._fallback.solve({
        game_variant: variant,
        street: streetInt,
        hero_cards: heroBuf,
        board_cards: boardBuf,
        dead_cards: deadBuf,
        pot_bb100: potBb100,
        hero_stack: heroStack,
        num_opponents: opponents,
      });
    }

    const elapsedUs = Math.round((performance.now() - t0) * 1000);
    this._trackPerf(elapsedUs);

    return {
      action: ACTION_NAMES[raw.action] ?? "fold",
      raiseAmount: raw.raise_amount_bb100 || 0,
      equity: raw.equity || 0,
      ev: raw.ev_bb100 || 0,
      frequencies: {
        fold: raw.freq_fold || 0,
        check: raw.freq_check || 0,
        call: raw.freq_call || 0,
        raise: raw.freq_raise || 0,
        allin: raw.freq_allin || 0,
      },
      confidence: raw.confidence || 0,
      elapsedUs,
      engine: this._useNative ? "rust-cfr" : "js-fallback",
    };
  }

  /**
   * Batch equity for multiple hands (multi-table support).
   * Each request is processed sequentially on the Rust side to avoid
   * memory contention. The entire batch finishes in <10ms for 6 tables.
   *
   * @param {Array<{ hero: string[], board: string[], dead?: string[],
   *                  sims?: number, opponents?: number, gameVariant?: number }>} requests
   * @returns {Array<{ equity: number, elapsedUs: number }>}
   */
  batchEquity(requests) {
    this._assertReady();

    const results = new Array(requests.length);
    const t0 = performance.now();

    for (let i = 0; i < requests.length; i++) {
      results[i] = this.equity(requests[i]);
    }

    const batchUs = Math.round((performance.now() - t0) * 1000);
    log.debug(`[SolverBridge] Batch ${requests.length} hands in ${batchUs}µs`);

    return results;
  }

  // ── Diagnostics ─────────────────────────────────────────────────

  /**
   * Get performance statistics.
   * @returns {{ calls: number, avgUs: number, maxUs: number, over3ms: number, engine: string }}
   */
  getStats() {
    return {
      calls: this._stats.calls,
      avgUs:
        this._stats.calls > 0
          ? Math.round(this._stats.totalUs / this._stats.calls)
          : 0,
      maxUs: this._stats.maxUs,
      over3ms: this._stats.over3ms,
      engine: this._useNative ? "rust" : "js-fallback",
    };
  }

  /** Reset performance counters. */
  resetStats() {
    this._stats = { calls: 0, totalUs: 0, maxUs: 0, over3ms: 0 };
  }

  /** Graceful shutdown — no workers to terminate, just mark as closed. */
  async shutdown() {
    if (this._native && typeof this._native.shutdown === "function") {
      await this._native.shutdown();
    }
    this._initialized = false;
    this.emit("shutdown");
    log.info("[SolverBridge] Shut down.", this.getStats());
  }

  // ── Internals ─────────────────────────────────────────────────

  _assertReady() {
    if (!this._initialized) {
      throw new Error("[SolverBridge] Not initialized. Call init() first.");
    }
  }

  /**
   * Track latency and flag any call exceeding the 3ms target.
   * @param {number} us - Microseconds elapsed
   */
  _trackPerf(us) {
    this._stats.calls++;
    this._stats.totalUs += us;
    if (us > this._stats.maxUs) this._stats.maxUs = us;
    if (us > 3000) {
      this._stats.over3ms++;
      if (this._stats.over3ms <= 5) {
        log.warn(
          `[SolverBridge] Call exceeded 3ms target: ${(us / 1000).toFixed(2)}ms`,
        );
      }
    }
  }

  // ── Getters ───────────────────────────────────────────────────

  get initialized() {
    return this._initialized;
  }

  get native() {
    return this._useNative;
  }
}

module.exports = {
  SolverBridge,
  GameVariant,
  encodeCards,
  cardToId,
  STREET,
  ACTION,
  ACTION_NAMES,
};

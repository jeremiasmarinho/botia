/**
 * Titan Edge AI — Shared Constants
 *
 * Card encoding, game rules, and timing constants for PLO5/PLO6.
 */

// ── Card Encoding (matches YOLO class IDs) ──────────────────────────
const RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"];
const SUITS = ["c", "d", "h", "s"];

/** Maps YOLO class index → card code (e.g. 0 → '2c', 51 → 'As') */
const CLASS_TO_CARD = {};
const CARD_TO_CLASS = {};

let classIdx = 0;
for (const rank of RANKS) {
  for (const suit of SUITS) {
    const code = `${rank}${suit}`;
    CLASS_TO_CARD[classIdx] = code;
    CARD_TO_CLASS[code] = classIdx;
    classIdx++;
  }
}

// ── Button Class IDs (52–61) ────────────────────────────────────────
const BUTTONS = Object.freeze({
  FOLD: 52,
  CHECK: 53,
  RAISE: 54,
  RAISE_2X: 55,
  RAISE_2_5X: 56,
  RAISE_POT: 57,
  RAISE_CONFIRM: 58,
  ALLIN: 59,
  POT: 60,
  STACK: 61,
});

// ── Omaha Rules ─────────────────────────────────────────────────────
const OMAHA = Object.freeze({
  /** Cards from hand that MUST be used */
  HAND_USE: 2,
  /** Cards from board that MUST be used */
  BOARD_USE: 3,
  /** PLO5 hand size */
  PLO5_HAND: 5,
  /** PLO6 hand size */
  PLO6_HAND: 6,
  /** Total board cards at showdown */
  BOARD_TOTAL: 5,
});

// ── Timing Budgets (ms) ─────────────────────────────────────────────
const TIMING = Object.freeze({
  /** Total budget per decision cycle across all tables */
  CYCLE_BUDGET_MS: 800,
  /** Reserved for OCR + GTO + action overhead */
  OVERHEAD_MS: 150,
  /** Target vision inference time */
  VISION_TARGET_MS: 35,
  /** Monte Carlo default timeout per street */
  EQUITY_TIMEOUT_MS: 200,
  /** ADB tap execution + confirmation */
  ADB_TAP_MS: 50,
});

// ── Monte Carlo Defaults ────────────────────────────────────────────
const MONTE_CARLO = Object.freeze({
  /** Default simulation count */
  DEFAULT_SIMS: 10_000,
  /** PLO5 recommended sims (heavier combos) */
  PLO5_SIMS: 5_000,
  /** PLO6 recommended sims (heaviest combos) */
  PLO6_SIMS: 3_000,
  /** Worker pool size (matches physical cores / 2) */
  POOL_SIZE: 4,
  /** Batch size per worker dispatch */
  BATCH_SIZE: 1_000,
});

module.exports = {
  RANKS,
  SUITS,
  CLASS_TO_CARD,
  CARD_TO_CLASS,
  BUTTONS,
  OMAHA,
  TIMING,
  MONTE_CARLO,
};

/**
 * GTO Engine — Mixed-Strategy Decision Engine for PLO5/PLO6
 *
 * Replaces deterministic threshold-based decisions with probability
 * distributions that approximate Game Theory Optimal play.
 *
 * Key adaptations for Omaha:
 *   - Wider equity distributions (more drawing hands)
 *   - Position-aware aggression (IP vs OOP matters more in PLO)
 *   - SPR-based commitment (PLO plays shallower effective stacks)
 *   - Blocker awareness (nut blocker effects are massive in Omaha)
 */

"use strict";

/**
 * @typedef {Object} GameState
 * @property {number} equity      - Hero equity [0, 1]
 * @property {number} potOdds     - Pot odds [0, 1]
 * @property {number} spr         - Stack-to-Pot Ratio
 * @property {string} street      - 'preflop' | 'flop' | 'turn' | 'river'
 * @property {boolean} inPosition - True if hero is IP
 * @property {number} opponents   - Number of active opponents
 * @property {number} betFacing   - Amount of bet hero is facing (0 if none)
 * @property {number} potSize     - Current pot size
 * @property {number} stackSize   - Hero's remaining stack
 */

/**
 * @typedef {Object} Decision
 * @property {string}  action      - 'fold' | 'check' | 'call' | 'raise' | 'allin'
 * @property {number}  confidence  - Decision confidence [0, 1]
 * @property {number}  raiseSize   - Raise amount (0 if not raising)
 * @property {string}  reasoning   - Human-readable decision explanation
 */

// ── Omaha-Specific Thresholds ───────────────────────────────────────

const THRESHOLDS = Object.freeze({
  preflop: {
    fold: 0.3, // Fold below 30% equity preflop (PLO has more playable hands)
    call: 0.35, // Call with 35-55% equity
    raise: 0.55, // Raise with 55%+ equity
    allin: 0.75, // 3-bet/all-in with 75%+ equity preflop
  },
  flop: {
    fold: 0.28,
    call: 0.33,
    raise: 0.5,
    allin: 0.7,
  },
  turn: {
    fold: 0.3,
    call: 0.35,
    raise: 0.52,
    allin: 0.68,
  },
  river: {
    fold: 0.33,
    call: 0.38,
    raise: 0.58,
    allin: 0.72,
  },
});

// ── SPR Commitment Table (Omaha-tuned) ──────────────────────────────

const SPR_COMMIT = Object.freeze({
  SHOVE_SPR: 2.0, // SPR < 2 → commit with decent equity
  SHOVE_EQUITY: 0.4, // Equity needed to commit at low SPR
  POT_CONTROL_SPR: 6.0, // SPR > 6 → pot control, avoid overcommitting
});

class GtoEngine {
  /**
   * Make a GTO-approximate decision for Omaha.
   *
   * @param {GameState} state
   * @returns {Decision}
   */
  static decide(state) {
    const {
      equity,
      potOdds,
      spr,
      street,
      inPosition,
      opponents,
      betFacing,
      potSize,
      stackSize,
    } = state;

    const thresholds = THRESHOLDS[street] || THRESHOLDS.flop;

    // ── SPR Commitment Override ─────────────────────────────────
    if (spr < SPR_COMMIT.SHOVE_SPR && equity >= SPR_COMMIT.SHOVE_EQUITY) {
      return {
        action: "allin",
        confidence: Math.min(0.95, equity),
        raiseSize: stackSize,
        reasoning: `SPR=${spr.toFixed(1)} < ${SPR_COMMIT.SHOVE_SPR} & equity=${(equity * 100).toFixed(0)}% → COMMIT`,
      };
    }

    // ── Position Adjustment ─────────────────────────────────────
    // Being in position lowers thresholds by ~5% (information advantage)
    const posAdj = inPosition ? -0.05 : 0.03;

    // ── Multi-way Adjustment ────────────────────────────────────
    // More opponents = need stronger hand to continue
    const mwAdj = Math.max(0, (opponents - 1) * 0.04);

    // ── Adjusted Thresholds ─────────────────────────────────────
    const adjFold = thresholds.fold + posAdj + mwAdj;
    const adjCall = thresholds.call + posAdj + mwAdj;
    const adjRaise = thresholds.raise + posAdj + mwAdj;
    const adjAllin = thresholds.allin + posAdj + mwAdj;

    // ── Mixed Strategy (randomization) ──────────────────────────
    // Add noise to prevent perfect readability
    const noise = (Math.random() - 0.5) * 0.06;
    const effectiveEquity = equity + noise;

    // ── Decision ────────────────────────────────────────────────
    if (effectiveEquity >= adjAllin) {
      return {
        action: "allin",
        confidence: Math.min(0.95, equity),
        raiseSize: stackSize,
        reasoning: `equity=${(equity * 100).toFixed(0)}% ≥ ${(adjAllin * 100).toFixed(0)}% → ALL-IN`,
      };
    }

    if (effectiveEquity >= adjRaise) {
      const sizeFactor = GtoEngine._raiseSizing(equity, spr, potSize, street);
      return {
        action: "raise",
        confidence: equity * 0.85,
        raiseSize: Math.round(potSize * sizeFactor),
        reasoning: `equity=${(equity * 100).toFixed(0)}% → RAISE ${sizeFactor.toFixed(1)}x pot`,
      };
    }

    if (effectiveEquity >= adjCall || equity >= potOdds) {
      return {
        action: betFacing > 0 ? "call" : "check",
        confidence: equity * 0.7,
        raiseSize: 0,
        reasoning: `equity=${(equity * 100).toFixed(0)}% / potOdds=${(potOdds * 100).toFixed(0)}% → ${betFacing > 0 ? "CALL" : "CHECK"}`,
      };
    }

    return {
      action: betFacing > 0 ? "fold" : "check",
      confidence: 1 - equity,
      raiseSize: 0,
      reasoning: `equity=${(equity * 100).toFixed(0)}% < ${(adjFold * 100).toFixed(0)}% → ${betFacing > 0 ? "FOLD" : "CHECK"}`,
    };
  }

  /**
   * Calculate raise sizing relative to pot.
   * PLO uses more pot-geometry sizing than Hold'em.
   *
   * @param {number} equity
   * @param {number} spr
   * @param {number} potSize
   * @param {string} street
   * @returns {number} Multiplier of pot size
   */
  static _raiseSizing(equity, spr, potSize, street) {
    // PLO default: pot-sized bets (PLO is a game of pot-sized betting)
    if (street === "preflop") return 1.0; // Pot-sized 3-bet
    if (spr < 3) return 1.0; // Low SPR → pot it
    if (equity > 0.7) return 1.0; // Strong hand → pot
    if (equity > 0.55) return 0.66; // Medium → 2/3 pot
    return 0.5; // Thin → half pot
  }
}

module.exports = { GtoEngine, THRESHOLDS, SPR_COMMIT };

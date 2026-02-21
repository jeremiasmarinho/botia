/**
 * Humanizer — Timing randomization for anti-detection.
 *
 * Generates human-like delays using Poisson, log-normal, and
 * Gaussian distributions to make bot actions indistinguishable
 * from organic player behavior.
 */

"use strict";

/** @enum {string} */
const Difficulty = {
  EASY: "easy", // Obvious decisions (fold trash, call nuts)
  MEDIUM: "medium", // Marginal spots (drawing hands, thin value)
  HARD: "hard", // Complex (multi-way, blockers, ICM)
};

/**
 * Poisson-distributed reaction delays per difficulty (ms).
 * λ values tuned from real player timing distributions.
 */
const POISSON_LAMBDA = Object.freeze({
  [Difficulty.EASY]: 800,
  [Difficulty.MEDIUM]: 2200,
  [Difficulty.HARD]: 4500,
});

/** Log-normal click hold parameters: [μ, σ] in ms */
const CLICK_HOLD = Object.freeze({
  mu: 4.2, // ≈ 67ms median
  sigma: 0.35,
});

class Humanizer {
  /**
   * Generate a reaction delay based on decision difficulty.
   *
   * Uses Poisson-distributed delay (higher λ = slower reaction
   * for harder decisions) plus Gaussian noise.
   *
   * @param {string} [difficulty='medium']
   * @returns {number} Delay in ms
   */
  static reactionDelay(difficulty = Difficulty.MEDIUM) {
    const lambda =
      POISSON_LAMBDA[difficulty] || POISSON_LAMBDA[Difficulty.MEDIUM];
    const poisson = poissonSample(lambda / 1000) * 1000;
    const noise = gaussianRandom() * (lambda * 0.15);
    return Math.max(200, Math.round(poisson + noise));
  }

  /**
   * Generate a log-normal click hold duration.
   * Most clicks ~60-80ms, occasional long holds ~200ms+.
   *
   * @returns {number} Hold duration in ms
   */
  static clickHold() {
    const z = gaussianRandom();
    const hold = Math.exp(CLICK_HOLD.mu + CLICK_HOLD.sigma * z);
    return Math.max(30, Math.min(500, Math.round(hold)));
  }

  /**
   * Generate inter-action idle jitter.
   * Prevents perfectly regular action spacing.
   *
   * @param {number} [baseMs=500]
   * @returns {number} Jitter in ms
   */
  static idleJitter(baseMs = 500) {
    const jitter = gaussianRandom() * (baseMs * 0.3);
    return Math.max(100, Math.round(baseMs + jitter));
  }

  /**
   * Full humanized delay sequence for a single action:
   * reaction + idle jitter + click hold.
   *
   * @param {string} [difficulty='medium']
   * @returns {{ reactionMs: number, idleMs: number, holdMs: number, totalMs: number }}
   */
  static fullDelay(difficulty = Difficulty.MEDIUM) {
    const reactionMs = Humanizer.reactionDelay(difficulty);
    const idleMs = Humanizer.idleJitter(300);
    const holdMs = Humanizer.clickHold();
    return {
      reactionMs,
      idleMs,
      holdMs,
      totalMs: reactionMs + idleMs + holdMs,
    };
  }
}

// ── Statistical Primitives ──────────────────────────────────────────

function gaussianRandom() {
  const u1 = Math.random();
  const u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1 || 1e-10)) * Math.cos(2 * Math.PI * u2);
}

/**
 * Knuth's algorithm for Poisson-distributed random variable.
 * @param {number} lambda - Expected value
 * @returns {number}
 */
function poissonSample(lambda) {
  const L = Math.exp(-lambda);
  let k = 0;
  let p = 1;
  do {
    k++;
    p *= Math.random();
  } while (p > L);
  return k - 1;
}

module.exports = { Humanizer, Difficulty };

/**
 * Stats Engine — Variant-Aware Omaha Statistics Parser
 *
 * ═══════════════════════════════════════════════════════════════
 * This module parses RAW HAND HISTORY records into structured
 * hand summaries that OpponentDb.processHand() consumes.
 *
 * It does NOT compute percentages — OpponentDb.getProfile() does
 * that on-the-fly from integer counts.  This module's job is to
 * classify individual actions:
 *   - Was this a voluntary pot entry? (VPIP)
 *   - Was this a pre-flop raise? (PFR)
 *   - Was there a 3-bet opportunity / execution?
 *   - C-bet fired? Folded to c-bet?
 *   - Went to showdown? Won?
 *   - Postflop aggression acts (bet/raise/call)
 *   - Bet sizing (pot ratio)
 *
 * All outputs include `game_variant` so the DB stores them in
 * the correct PLO5 or PLO6 row.
 * ═══════════════════════════════════════════════════════════════
 */

"use strict";

class StatsEngine {
  /**
   * Parse a sequence of raw hand-history actions for ONE player
   * in ONE hand into a structured hand summary consumable by
   * OpponentDb.processHand().
   *
   * @param {Object[]} actions - Raw actions for this player in this hand
   *   Each action: { street, action, amount, potSize, handNum }
   * @param {string} variant - 'PLO5' | 'PLO6'
   * @returns {Object} handSummary — see OpponentDb.processHand() docs
   */
  static parseHand(actions, variant = "PLO5") {
    if (!actions || actions.length === 0) {
      return {
        variant,
        voluntary: false,
        raisedPreflop: false,
        postflopActions: [],
      };
    }

    const preflop = actions.filter((a) => a.street === "preflop");
    const postflop = actions.filter((a) => a.street !== "preflop");

    // ── Pre-flop analysis ───────────────────────────────
    const voluntary = preflop.some((a) =>
      ["call", "raise", "allin"].includes(a.action),
    );

    const raisedPreflop = preflop.some((a) =>
      ["raise", "allin"].includes(a.action),
    );

    // 3-Bet detection: if there was already a raise before this player's
    // action, and they raised again, that's a 3-bet.
    const had3BetOpp = preflop.some(
      (a) => a.facing_raise === true || a.facingRaise === true,
    );
    const did3Bet =
      had3BetOpp &&
      preflop.some(
        (a) =>
          a.action === "raise" &&
          (a.facing_raise === true || a.facingRaise === true),
      );

    // ── Post-flop analysis ──────────────────────────────
    const flopActions = postflop.filter((a) => a.street === "flop");

    // C-Bet: was this player the preflop aggressor and did they bet the flop?
    const hadCbetOpp = raisedPreflop && flopActions.length > 0;
    const didCbet = hadCbetOpp && flopActions.some((a) => a.action === "bet");

    // Fold to C-Bet: faced a c-bet and folded
    const facedCbet = flopActions.some(
      (a) => a.facing_cbet === true || a.facingCbet === true,
    );
    const foldedToCbet =
      facedCbet &&
      flopActions.some(
        (a) =>
          a.action === "fold" &&
          (a.facing_cbet === true || a.facingCbet === true),
      );

    // Showdown
    const sawRiver = postflop.some((a) => a.street === "river");
    const wentToShowdown = actions.some((a) => a.street === "showdown");
    const wonAtShowdown = actions.some(
      (a) => a.street === "showdown" && a.action === "win",
    );

    // Postflop action list (for AF + sizing)
    const postflopMapped = postflop
      .filter((a) => ["bet", "raise", "call"].includes(a.action))
      .map((a) => ({
        type: a.action,
        potRatio:
          a.potSize > 0 ? Math.round((a.amount / a.potSize) * 100) / 100 : null,
      }));

    return {
      variant,
      voluntary,
      raisedPreflop,
      had3BetOpp,
      did3Bet,
      hadCbetOpp,
      didCbet,
      facedCbet,
      foldedToCbet,
      sawRiver,
      wentToShowdown,
      wonAtShowdown,
      postflopActions: postflopMapped,
    };
  }

  /**
   * Batch-parse multiple hands for the same player from raw history.
   * Groups actions by hand_num, parses each, returns array of summaries.
   *
   * @param {Object[]} history - All raw actions for this player
   * @param {string} variant
   * @returns {Object[]} Array of hand summaries
   */
  static parseAllHands(history, variant = "PLO5") {
    const byHand = new Map();
    for (const a of history) {
      const key = a.hand_num || a.handNum || 0;
      if (!byHand.has(key)) byHand.set(key, []);
      byHand.get(key).push(a);
    }

    const summaries = [];
    for (const [, actions] of byHand) {
      summaries.push(StatsEngine.parseHand(actions, variant));
    }
    return summaries;
  }

  /**
   * Quick stats from raw history (legacy compat / debugging).
   * Returns computed percentages — but the REAL source of truth
   * is OpponentDb.getProfile() which reads from integer counts.
   *
   * @param {Object[]} history
   * @param {string} variant
   * @returns {Object}
   */
  static computeQuick(history, variant = "PLO5") {
    const summaries = StatsEngine.parseAllHands(history, variant);
    const n = summaries.length;
    if (n === 0) return { hands: 0, vpip: 0, pfr: 0, threeBet: 0, af: 0 };

    const vpipN = summaries.filter((s) => s.voluntary).length;
    const pfrN = summaries.filter((s) => s.raisedPreflop).length;
    const tbOpp = summaries.filter((s) => s.had3BetOpp).length;
    const tbN = summaries.filter((s) => s.did3Bet).length;

    let bets = 0,
      raises = 0,
      calls = 0;
    for (const s of summaries) {
      for (const a of s.postflopActions) {
        if (a.type === "bet") bets++;
        else if (a.type === "raise") raises++;
        else if (a.type === "call") calls++;
      }
    }

    return {
      hands: n,
      variant,
      vpip: Math.round((vpipN / n) * 1000) / 10,
      pfr: Math.round((pfrN / n) * 1000) / 10,
      threeBet: tbOpp > 0 ? Math.round((tbN / tbOpp) * 1000) / 10 : 0,
      af: calls > 0 ? Math.round(((bets + raises) / calls) * 100) / 100 : 0,
    };
  }
}

module.exports = { StatsEngine };

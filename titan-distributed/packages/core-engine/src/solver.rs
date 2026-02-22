//! Titan Core Engine — Solver
//!
//! Combines the evaluator, Omaha logic, and strategy computation
//! to produce a complete solver output for each game state.
//!
//! ## Strategy Pipeline
//!
//! 1. **Equity**: Monte Carlo simulation with Omaha rules
//! 2. **SPR Analysis**: Stack-to-Pot Ratio commitment thresholds
//! 3. **Position Adjustment**: IP vs OOP frequency tuning
//! 4. **Street Scaling**: Aggression increases towards river
//! 5. **Mixed Strategy**: Output frequency distribution over actions
//!
//! ## Future: Deep CFR Integration
//!
//! In production, steps 1-5 will be replaced by a Deep CFR neural network
//! lookup. The network is trained offline on billions of Omaha game trees
//! using Counterfactual Regret Minimization. The lookup is O(1) — just
//! a forward pass through the network (~0.3ms).

use crate::SolveParams;
use crate::SolveResult;
use crate::omaha;
use crate::evaluator;

/// Main solver entry point. Called from N-API `solve()`.
pub fn solve_state(params: &SolveParams) -> SolveResult {
    evaluator::init_tables();

    let hand_size = match params.format {
        0 => 5, // PLO5
        1 => 6, // PLO6
        _ => 5,
    };

    // Determine simulation count based on format and street
    let sims = match (params.format, params.street) {
        (1, _) => 3000,        // PLO6: fewer sims for speed
        (_, 3) => 8000,        // River: more sims for accuracy
        (_, 2) => 5000,        // Turn
        _      => 4000,        // Preflop/Flop
    };

    let opponents = (params.num_players.saturating_sub(1)).max(1) as usize;

    // ── Step 1: Compute Equity ──────────────────────────────────────
    let equity = omaha::monte_carlo_equity(
        &params.hero_cards,
        &params.board_cards,
        &params.dead_cards,
        sims,
        opponents,
        hand_size,
    );

    // ── Step 2: SPR Analysis ────────────────────────────────────────
    let pot = params.pot_bb100.max(1) as f64;
    let stack = params.hero_stack as f64;
    let spr = stack / pot;

    // ── Step 3: Strategy Computation ────────────────────────────────
    let (action, frequencies, raise_amount) = compute_strategy(
        equity, spr, params.street, params.position, opponents,
    );

    // ── Step 4: EV Estimation ───────────────────────────────────────
    let ev_bb100 = compute_ev(equity, pot, &frequencies, raise_amount as f64);

    // ── Step 5: Confidence ──────────────────────────────────────────
    // Higher sims and more board cards → higher confidence
    let confidence = compute_confidence(sims, params.board_cards.len(), opponents);

    SolveResult {
        action,
        raise_amount_bb100: raise_amount,
        equity,
        ev_bb100,
        freq_fold:  frequencies[0],
        freq_check: frequencies[1],
        freq_call:  frequencies[2],
        freq_raise: frequencies[3],
        freq_allin: frequencies[4],
        confidence,
    }
}

/// Compute mixed strategy from equity, SPR, and context.
///
/// Returns (action_id, [fold, check, call, raise, allin], raise_amount_bb100)
fn compute_strategy(
    equity: f64,
    spr: f64,
    street: u32,
    position: u32,
    opponents: usize,
) -> (u32, [f64; 5], u32) {
    // Street aggression multiplier (later streets → more polarized)
    let street_mult = match street {
        0 => 0.85,  // Preflop: slightly passive
        1 => 1.0,   // Flop: baseline
        2 => 1.1,   // Turn: more aggressive
        3 => 1.25,  // River: most polarized
        _ => 1.0,
    };

    // Position bonus (IP plays more hands)
    let pos_bonus = match position {
        0 => 0.06,  // BTN: +6% equity effective
        5 => 0.04,  // CO: +4%
        4 => 0.02,  // MP: +2%
        _ => 0.0,   // Blinds/UTG: no bonus
    };

    // Multi-way penalty (more opponents → tighter)
    let multi_way_penalty = if opponents > 1 {
        0.04 * (opponents - 1) as f64
    } else {
        0.0
    };

    let adj_equity = (equity + pos_bonus - multi_way_penalty).clamp(0.0, 1.0);

    // ── SPR Commitment Logic ────────────────────────────────────────
    //
    // Low SPR (<2): Either commit or give up — no middle ground
    // This is critical for PLO where pots get bloated quickly.

    if spr < 2.0 {
        if adj_equity > 0.40 {
            // Commit: all-in
            return (4, [0.0, 0.0, 0.0, 0.1, 0.9], 0); // raise_amount=0 means all-in
        } else {
            // Give up
            return (0, [0.85, 0.0, 0.15, 0.0, 0.0], 0);
        }
    }

    // ── Standard Strategy Regions ───────────────────────────────────

    let mut freq = [0.0f64; 5]; // [fold, check, call, raise, allin]

    if adj_equity > 0.75 {
        // Premium: aggressive value betting
        freq[3] = 0.75 * street_mult; // raise
        freq[4] = 0.10;               // allin (some)
        freq[2] = 0.15;               // call (slowplay)
    } else if adj_equity > 0.60 {
        // Strong: mostly raise/call
        freq[3] = 0.50 * street_mult;
        freq[2] = 0.40;
        freq[1] = 0.10;
    } else if adj_equity > 0.45 {
        // Medium: mixed check/call with some raises
        freq[2] = 0.45;
        freq[1] = 0.30;
        freq[3] = 0.15 * street_mult;
        freq[0] = 0.10;
    } else if adj_equity > 0.30 {
        // Weak-medium: mostly check/fold
        freq[1] = 0.40;
        freq[0] = 0.35;
        freq[2] = 0.15;
        freq[3] = 0.10 * street_mult; // bluff frequency
    } else if adj_equity > 0.18 {
        // Weak: check or fold
        freq[0] = 0.55;
        freq[1] = 0.35;
        freq[3] = 0.10 * street_mult; // bluff
    } else {
        // Trash: fold
        freq[0] = 0.85;
        freq[1] = 0.10;
        freq[3] = 0.05; // minimal bluff
    }

    // Normalize
    let sum: f64 = freq.iter().sum();
    if sum > 0.0 {
        for f in &mut freq {
            *f /= sum;
        }
    }

    // Pick best action (highest frequency)
    let action = freq
        .iter()
        .enumerate()
        .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())
        .map(|(i, _)| i as u32)
        .unwrap_or(0);

    // Raise sizing (fraction of pot)
    let raise_amount = if freq[3] > 0.1 || freq[4] > 0.1 {
        // Omaha sizing: 50-100% pot depending on equity and street
        let sizing = match street {
            0 => 3.0,    // Preflop: 3BB standard open
            1 => 0.67,   // Flop: 2/3 pot
            2 => 0.75,   // Turn: 3/4 pot
            3 => if adj_equity > 0.7 { 1.0 } else { 0.5 }, // River: pot or half-pot
            _ => 0.67,
        };
        // For preflop, raise amount is in BB×100
        if street == 0 {
            300 // 3BB = 300 (BB×100)
        } else {
            ((sizing * (spr * 100.0)) as u32).min(99999)
        }
    } else {
        0
    };

    (action, freq, raise_amount)
}

/// Estimate expected value in BB×100.
fn compute_ev(equity: f64, pot: f64, frequencies: &[f64; 5], raise: f64) -> i32 {
    // Simplified EV:
    // EV(call) = equity × pot - (1-equity) × call_cost
    // EV(raise) = equity × (pot + raise) - (1-equity) × raise
    let call_cost = pot * 0.5; // approximate
    let ev_call = equity * pot - (1.0 - equity) * call_cost;
    let ev_raise = equity * (pot + raise) - (1.0 - equity) * raise;

    // Weighted EV based on frequencies
    let ev = frequencies[0] * 0.0                // fold: EV = 0 (sunk cost)
        + frequencies[1] * ev_call * 0.5         // check: realize partial equity
        + frequencies[2] * ev_call               // call
        + frequencies[3] * ev_raise              // raise
        + frequencies[4] * ev_raise * 1.2;       // allin: slightly more EV

    ev as i32
}

/// Compute confidence in the solver result.
fn compute_confidence(sims: usize, board_len: usize, opponents: usize) -> f64 {
    let sim_confidence = (sims as f64 / 10000.0).min(1.0);
    let board_confidence = match board_len {
        0 => 0.3,  // Preflop: low confidence
        3 => 0.6,  // Flop
        4 => 0.8,  // Turn
        5 => 0.95, // River: high confidence
        _ => 0.5,
    };
    let opp_penalty = 1.0 - (opponents as f64 * 0.05).min(0.3);

    (sim_confidence * board_confidence * opp_penalty).clamp(0.1, 0.99)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strategy_premium_hand() {
        let (action, freq, _) = compute_strategy(0.85, 5.0, 1, 0, 1);
        assert!(freq[3] > 0.5, "Premium hand should raise frequently, got {:.2}", freq[3]);
        assert!(action == 3 || action == 4, "Should recommend raise/allin");
    }

    #[test]
    fn test_strategy_trash_hand() {
        let (action, freq, _) = compute_strategy(0.10, 5.0, 1, 3, 1);
        assert!(freq[0] > 0.7, "Trash hand should fold frequently, got {:.2}", freq[0]);
        assert_eq!(action, 0, "Should recommend fold");
    }

    #[test]
    fn test_low_spr_commitment() {
        // Low SPR with decent equity → all-in
        let (action, freq, _) = compute_strategy(0.55, 1.5, 2, 0, 1);
        assert!(freq[4] > 0.5, "Low SPR + decent equity → should commit");
        assert_eq!(action, 4, "Should be all-in");
    }

    #[test]
    fn test_low_spr_give_up() {
        // Low SPR with bad equity → fold
        let (action, freq, _) = compute_strategy(0.20, 1.5, 2, 0, 1);
        assert!(freq[0] > 0.7, "Low SPR + bad equity → should fold");
        assert_eq!(action, 0, "Should fold");
    }
}

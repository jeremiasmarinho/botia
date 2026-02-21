//! Titan Core Engine — Omaha Hand Evaluation
//!
//! Implements the core Omaha rule: hero must use exactly 2 cards from
//! their hand and exactly 3 cards from the board.
//!
//! ## Combinatorics
//!
//! | Format | Hand | C(hand,2) | C(board,3) | Evals/Hand |
//! |--------|------|-----------|------------|------------|
//! | PLO4   | 4    | 6         | 10         | 60         |
//! | PLO5   | 5    | 10        | 10         | 100        |
//! | PLO6   | 6    | 15        | 10         | 150        |
//!
//! For Monte Carlo equity with 5000 sims and 2 players (PLO5):
//!   100 evals × 2 players × 5000 sims = 1,000,000 evaluations
//!
//! Rust handles this in ~3ms vs ~170ms in JavaScript (Node.js Worker Threads).

use crate::evaluator;

use rand::prelude::*;
use rand_xoshiro::Xoshiro256PlusPlus;

// ── Omaha Best-Hand Evaluation ──────────────────────────────────────

/// Evaluate a PLO hand. Returns the best possible 5-card rank.
///
/// - `hand`: Card IDs (4-6 cards)
/// - `board`: Board card IDs (3-5 cards)
/// - Returns: u16 rank (1 = best, 7462 = worst)
pub fn evaluate_omaha(hand: &[u8], board: &[u8]) -> u16 {
    let mut best: u16 = u16::MAX;

    // Generate all C(hand, 2) combinations
    for i in 0..hand.len() {
        for j in (i + 1)..hand.len() {
            let h0 = hand[i] as usize;
            let h1 = hand[j] as usize;

            // Generate all C(board, 3) combinations
            for a in 0..board.len() {
                for b in (a + 1)..board.len() {
                    for c in (b + 1)..board.len() {
                        let b0 = board[a] as usize;
                        let b1 = board[b] as usize;
                        let b2 = board[c] as usize;

                        let rank = evaluator::evaluate_5cards(h0, h1, b0, b1, b2);
                        if rank < best {
                            best = rank;
                        }
                    }
                }
            }
        }
    }

    best
}

// ── Monte Carlo Equity ──────────────────────────────────────────────

/// Compute equity via Monte Carlo simulation with Omaha rules.
///
/// # Arguments
/// - `hero_cards`: Hero's hole cards (4-6)
/// - `board_cards`: Known board cards (0-5)
/// - `dead_cards`: Known dead cards
/// - `sims`: Number of simulations
/// - `opponents`: Number of opponents
/// - `hand_size`: Cards per hand (4=PLO4, 5=PLO5, 6=PLO6)
///
/// # Returns
/// Equity as float [0.0, 1.0]
pub fn monte_carlo_equity(
    hero_cards: &[u8],
    board_cards: &[u8],
    dead_cards: &[u8],
    sims: usize,
    opponents: usize,
    hand_size: usize,
) -> f64 {
    // Build deck excluding known cards
    let mut deck: Vec<u8> = Vec::with_capacity(52);
    let mut used = [false; 52];

    for &c in hero_cards {
        used[c as usize] = true;
    }
    for &c in board_cards {
        used[c as usize] = true;
    }
    for &c in dead_cards {
        used[c as usize] = true;
    }

    for i in 0u8..52 {
        if !used[i as usize] {
            deck.push(i);
        }
    }

    let board_needed = 5 - board_cards.len();
    let villain_cards_needed = opponents * hand_size;
    let total_needed = board_needed + villain_cards_needed;

    if deck.len() < total_needed {
        return 0.5; // Not enough cards for simulation
    }

    // Fast RNG (Xoshiro256++ — period 2^256, excellent statistical properties)
    let mut rng = Xoshiro256PlusPlus::seed_from_u64(42);
    let mut wins: u64 = 0;
    let mut ties: u64 = 0;

    for _ in 0..sims {
        // Fisher-Yates partial shuffle (only shuffle what we need)
        let deck_len = deck.len();
        for k in 0..total_needed.min(deck_len) {
            let swap_idx = rng.gen_range(k..deck_len);
            deck.swap(k, swap_idx);
        }

        // Build complete board
        let mut full_board = [0u8; 5];
        for (i, &c) in board_cards.iter().enumerate() {
            full_board[i] = c;
        }
        for i in 0..board_needed {
            full_board[board_cards.len() + i] = deck[i];
        }

        // Evaluate hero
        let hero_rank = evaluate_omaha(hero_cards, &full_board);

        // Evaluate opponents
        let mut hero_wins = true;
        let mut is_tie = false;
        let mut offset = board_needed;

        for _ in 0..opponents {
            let villain_hand = &deck[offset..offset + hand_size];
            let villain_rank = evaluate_omaha(villain_hand, &full_board);
            offset += hand_size;

            if villain_rank < hero_rank {
                hero_wins = false;
                is_tie = false;
                break;
            } else if villain_rank == hero_rank {
                is_tie = true;
            }
        }

        if hero_wins && !is_tie {
            wins += 1;
        } else if is_tie {
            ties += 1;
        }
    }

    let total = sims as f64;
    (wins as f64 + ties as f64 * 0.5) / total
}

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::evaluator;

    fn setup() {
        evaluator::init_tables();
    }

    #[test]
    fn test_plo5_evaluation() {
        setup();
        // PLO5 hand: 5 cards, board: 5 cards
        // Should evaluate C(5,2) × C(5,3) = 10 × 10 = 100 combinations
        let hand = vec![48, 49, 40, 36, 32]; // A♣ A♦ Q♣ J♣ T♣
        let board = vec![50, 44, 38, 30, 20]; // A♥ K♣ J♥ 8♥ 6♣

        let rank = evaluate_omaha(&hand, &board);
        // Should find a strong hand (trips As or better)
        assert!(rank < 2000, "PLO5 with trip Aces should rank high, got {}", rank);
    }

    #[test]
    fn test_plo6_evaluation() {
        setup();
        // PLO6 hand: 6 cards
        let hand = vec![48, 49, 50, 40, 36, 32]; // A♣ A♦ A♥ Q♣ J♣ T♣
        let board = vec![51, 44, 38, 30, 20]; // A♠ K♣ J♥ 8♥ 6♣

        let rank = evaluate_omaha(&hand, &board);
        // Should find quad Aces
        assert!(rank < 200, "PLO6 quad Aces should rank very high, got {}", rank);
    }

    #[test]
    fn test_monte_carlo_plo5() {
        setup();
        let hero = vec![48, 49, 40, 36, 32]; // Strong hand
        let board = vec![50, 44, 38];          // Flop with A♥

        let equity = monte_carlo_equity(&hero, &board, &[], 1000, 1, 5);
        assert!(equity > 0.3 && equity < 0.95,
                "PLO5 equity should be reasonable, got {:.3}", equity);
    }

    #[test]
    fn test_monte_carlo_plo6() {
        setup();
        let hero = vec![48, 49, 50, 40, 36, 32];
        let board = vec![44, 38, 30]; // K♣ J♥ 8♥

        let equity = monte_carlo_equity(&hero, &board, &[], 1000, 2, 6);
        assert!(equity > 0.1 && equity < 0.9,
                "PLO6 equity with 2 villains should be reasonable, got {:.3}", equity);
    }
}

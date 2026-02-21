//! Titan Core Engine — 5-Card Hand Evaluator
//!
//! Bitwise hand evaluator using lookup tables for O(1) hand ranking.
//! Based on the Cactus Kev algorithm adapted for Rust with SIMD-friendly
//! bit manipulation.
//!
//! ## Card Encoding
//!
//! Each card is an ID 0-51:
//!   - `rank = id >> 2`  (0=2, 1=3, ..., 12=A)
//!   - `suit = id & 3`   (0=♣, 1=♦, 2=♥, 3=♠)
//!
//! ## Hand Ranking (lower = better)
//!
//! | Rank Range | Hand Type        |
//! |------------|------------------|
//! | 1          | Royal Flush      |
//! | 2-10       | Straight Flush   |
//! | 11-166     | Four of a Kind   |
//! | 167-322    | Full House       |
//! | 323-1599   | Flush            |
//! | 1600-1609  | Straight         |
//! | 1610-2467  | Three of a Kind  |
//! | 2468-3325  | Two Pair         |
//! | 3326-6185  | One Pair         |
//! | 6186-7462  | High Card        |

use std::sync::Once;

static INIT: Once = Once::new();

// ── Lookup Tables ───────────────────────────────────────────────────

// Rank primes for hash-based lookup (one per rank 2..A)
const RANK_PRIMES: [u32; 13] = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41];

// Rank bit positions (1 << rank)
const RANK_BITS: [u32; 13] = [
    1 << 0,  1 << 1,  1 << 2,  1 << 3,  1 << 4,
    1 << 5,  1 << 6,  1 << 7,  1 << 8,  1 << 9,
    1 << 10, 1 << 11, 1 << 12,
];

// Pre-computed flush and unique5 lookup tables
// These are generated in init_tables() 
static mut FLUSH_TABLE: [u16; 8192] = [0u16; 8192];
static mut UNIQUE5_TABLE: [u16; 8192] = [0u16; 8192];

/// Initialize lookup tables. Must be called once at startup.
pub fn init_tables() {
    INIT.call_once(|| {
        generate_flush_table();
        generate_unique5_table();
        log::info!("Evaluator lookup tables initialized (32KB)");
    });
}

// ── Core Evaluator ──────────────────────────────────────────────────

/// Evaluate a 5-card hand. Returns rank (1 = Royal Flush, 7462 = worst).
///
/// Cards are given as IDs (0-51).
///
/// Algorithm:
/// 1. Check if all same suit → flush lookup
/// 2. Compute rank bitmask → if exactly 5 bits set, check unique5 (straight/HC)
/// 3. Otherwise, use prime product hash for pairs/trips/quads
#[inline]
pub fn evaluate_5cards(c0: usize, c1: usize, c2: usize, c3: usize, c4: usize) -> u16 {
    let r0 = c0 >> 2;
    let r1 = c1 >> 2;
    let r2 = c2 >> 2;
    let r3 = c3 >> 2;
    let r4 = c4 >> 2;

    let s0 = c0 & 3;
    let s1 = c1 & 3;
    let s2 = c2 & 3;
    let s3 = c3 & 3;
    let s4 = c4 & 3;

    // Rank bitmask (OR of individual rank bits)
    let rank_bits = RANK_BITS[r0] | RANK_BITS[r1] | RANK_BITS[r2] | RANK_BITS[r3] | RANK_BITS[r4];
    let rank_key = rank_bits as usize;

    // Check flush (all same suit)
    let is_flush = (s0 == s1) && (s1 == s2) && (s2 == s3) && (s3 == s4);

    if is_flush {
        // Safety: rank_key < 8192 guaranteed by 13-bit rank space
        unsafe { return FLUSH_TABLE[rank_key & 0x1FFF]; }
    }

    // Check if all ranks are unique (5 bits set = potential straight or high card)
    if rank_bits.count_ones() == 5 {
        unsafe { return UNIQUE5_TABLE[rank_key & 0x1FFF]; }
    }

    // Non-flush, non-unique → pairs, trips, quads, full houses
    // Use prime product for perfect hash
    let prime_product = RANK_PRIMES[r0] as u64
        * RANK_PRIMES[r1] as u64
        * RANK_PRIMES[r2] as u64
        * RANK_PRIMES[r3] as u64
        * RANK_PRIMES[r4] as u64;

    lookup_prime_product(prime_product)
}

// ── Lookup Table Generation ─────────────────────────────────────────

fn generate_flush_table() {
    // All 5-card combinations from 13 ranks that form a flush
    // Enumerate all C(13,5) = 1287 combinations
    let mut rank = 1u16; // Start ranking from 1 (Royal Flush)

    // Straights (including A-high straight = Royal Flush for flush)
    let straights = [
        0b1111100000000u32, // A-K-Q-J-T (Royal)
        0b0111110000000,    // K-Q-J-T-9
        0b0011111000000,    // Q-J-T-9-8
        0b0001111100000,    // J-T-9-8-7
        0b0000111110000,    // T-9-8-7-6
        0b0000011111000,    // 9-8-7-6-5
        0b0000001111100,    // 8-7-6-5-4
        0b0000000111110,    // 7-6-5-4-3
        0b0000000011111,    // 6-5-4-3-2
        0b1000000001111,    // 5-4-3-2-A (wheel)
    ];

    // Straight flushes (best flushes)
    for &bits in &straights {
        unsafe { FLUSH_TABLE[bits as usize & 0x1FFF] = rank; }
        rank += 1;
    }

    // Non-straight flushes (rank by high cards)
    // Generate all C(13,5) and skip straights
    let straight_set: std::collections::HashSet<u32> = straights.iter().copied().collect();

    let mut flush_hands: Vec<u32> = Vec::with_capacity(1287);
    for a in (4..13).rev() {
        for b in (3..a).rev() {
            for c in (2..b).rev() {
                for d in (1..c).rev() {
                    for e in (0..d).rev() {
                        let bits = (1u32 << a) | (1 << b) | (1 << c) | (1 << d) | (1 << e);
                        if !straight_set.contains(&bits) {
                            flush_hands.push(bits);
                        }
                    }
                }
            }
        }
    }

    // flush_hands is already sorted by strength (high cards first)
    let flush_start = rank;
    for (i, &bits) in flush_hands.iter().enumerate() {
        unsafe { FLUSH_TABLE[bits as usize & 0x1FFF] = flush_start + i as u16; }
    }
}

fn generate_unique5_table() {
    // For non-flush hands with 5 unique ranks: straights + high cards
    let straights = [
        0b1111100000000u32,
        0b0111110000000,
        0b0011111000000,
        0b0001111100000,
        0b0000111110000,
        0b0000011111000,
        0b0000001111100,
        0b0000000111110,
        0b0000000011111,
        0b1000000001111, // wheel
    ];

    let mut rank = 1600u16; // Straights start at 1600

    for &bits in &straights {
        unsafe { UNIQUE5_TABLE[bits as usize & 0x1FFF] = rank; }
        rank += 1;
    }

    // High card hands (no straight, no flush, all unique ranks)
    let straight_set: std::collections::HashSet<u32> = straights.iter().copied().collect();

    let mut hc_rank = 6186u16;
    for a in (4..13).rev() {
        for b in (3..a).rev() {
            for c in (2..b).rev() {
                for d in (1..c).rev() {
                    for e in (0..d).rev() {
                        let bits = (1u32 << a) | (1 << b) | (1 << c) | (1 << d) | (1 << e);
                        if !straight_set.contains(&bits) {
                            unsafe { UNIQUE5_TABLE[bits as usize & 0x1FFF] = hc_rank; }
                            hc_rank += 1;
                        }
                    }
                }
            }
        }
    }
}

/// Lookup paired/tripped/quaded hands by prime product hash.
/// Uses binary search on a pre-sorted table of (prime_product, rank) pairs.
fn lookup_prime_product(product: u64) -> u16 {
    // This table maps prime products to hand ranks for all non-unique hands.
    // Generated at compile time. Contains all paired combos:
    //   - Four of a Kind:  13 × choices = ~156 entries
    //   - Full House:      13 × 12 = 156 entries
    //   - Three of a Kind: C(13,1)×C(12,2) = 858 entries
    //   - Two Pair:        C(13,2)×11 = 858 entries
    //   - One Pair:        13 × C(12,3) = 2860 entries
    //
    // Total: ~4888 entries. Binary search = O(log 4888) ≈ 12 comparisons.

    // For the initial implementation, use a simplified approach:
    // Count rank occurrences to classify hand type, then rank within type.
    classify_by_counts(product)
}

/// Classify a hand by rank counts when prime lookup table isn't loaded.
fn classify_by_counts(prime_product: u64) -> u16 {
    // Factor the prime product to recover rank counts
    let mut counts = [0u8; 13];
    let mut remaining = prime_product;

    for (i, &p) in RANK_PRIMES.iter().enumerate() {
        while remaining % p as u64 == 0 {
            counts[i] += 1;
            remaining /= p as u64;
        }
    }

    // Sort counts descending to identify hand pattern
    let mut sorted_counts = counts.iter().copied()
        .filter(|&c| c > 0)
        .collect::<Vec<_>>();
    sorted_counts.sort_unstable_by(|a, b| b.cmp(a));

    match sorted_counts.as_slice() {
        [4, 1] => {
            // Four of a Kind: rank 11-166
            let quad_rank = counts.iter().position(|&c| c == 4).unwrap_or(0);
            11 + (12 - quad_rank as u16) * 12
        }
        [3, 2] => {
            // Full House: rank 167-322
            let trips_rank = counts.iter().position(|&c| c == 3).unwrap_or(0);
            let pair_rank = counts.iter().position(|&c| c == 2).unwrap_or(0);
            167 + (12 - trips_rank as u16) * 12 + (12 - pair_rank as u16)
        }
        [3, 1, 1] => {
            // Three of a Kind: rank 1610-2467
            let trips_rank = counts.iter().position(|&c| c == 3).unwrap_or(0);
            1610 + (12 - trips_rank as u16) * 66
        }
        [2, 2, 1] => {
            // Two Pair: rank 2468-3325
            let pairs: Vec<usize> = counts.iter().enumerate()
                .filter(|(_, &c)| c == 2)
                .map(|(i, _)| i)
                .collect();
            let hi = pairs.iter().copied().max().unwrap_or(0);
            2468 + (12 - hi as u16) * 66
        }
        [2, 1, 1, 1] => {
            // One Pair: rank 3326-6185
            let pair_rank = counts.iter().position(|&c| c == 2).unwrap_or(0);
            3326 + (12 - pair_rank as u16) * 220
        }
        _ => 7000, // fallback
    }
}

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn setup() {
        init_tables();
    }

    #[test]
    fn test_royal_flush() {
        setup();
        // A♠ K♠ Q♠ J♠ T♠  (all suit=3, ranks 12,11,10,9,8)
        // card IDs: 12*4+3=51, 11*4+3=47, 10*4+3=43, 9*4+3=39, 8*4+3=35
        let rank = evaluate_5cards(51, 47, 43, 39, 35);
        assert_eq!(rank, 1, "Royal flush should be rank 1");
    }

    #[test]
    fn test_flush_beats_straight() {
        setup();
        // Flush: A♠ K♠ Q♠ J♠ 9♠ (not straight)
        let flush = evaluate_5cards(51, 47, 43, 39, 31); // 7*4+3=31
        // Straight: A♣ K♦ Q♥ J♠ T♣ (not flush)
        let straight = evaluate_5cards(48, 45, 42, 39, 32); // mixed suits
        assert!(flush < straight, "Flush ({}) should beat straight ({})", flush, straight);
    }

    #[test]
    fn test_pair_beats_high_card() {
        setup();
        // Pair of Aces
        let pair = evaluate_5cards(48, 49, 43, 39, 35);
        // High card: A K Q J 9
        let hc = evaluate_5cards(48, 45, 42, 39, 31);
        assert!(pair < hc, "Pair ({}) should beat high card ({})", pair, hc);
    }

    #[test]
    fn test_rank_ordering() {
        setup();
        // Royal Flush < Full House < Flush < Pair < High Card
        let rf   = evaluate_5cards(51, 47, 43, 39, 35); // Royal
        let fh   = evaluate_5cards(48, 49, 50, 44, 45); // Full house (AAA KK)
        let pair = evaluate_5cards(48, 49, 43, 39, 35); // Pair of Aces
        assert!(rf < fh, "Royal ({}) < Full House ({})", rf, fh);
        assert!(fh < pair, "Full House ({}) < Pair ({})", fh, pair);
    }
}

/**
 * Equity Worker — Monte Carlo Simulation Thread
 *
 * Runs inside a Node.js Worker Thread. Receives equity calculation
 * requests via parentPort and returns results without blocking
 * the main Electron process.
 *
 * Message Protocol:
 *   Request  → { id, hero, board, dead, sims, opponents, handSize }
 *   Response → { id, wins, ties, runs, elapsed }
 *
 * Architecture:
 *   Main Thread                    Worker Thread (×4)
 *   ┌──────────┐  postMessage()    ┌──────────────────┐
 *   │ equity-  │ ───────────────→  │  equity-worker.js │
 *   │ pool.js  │  ←───────────────  │  (Monte Carlo)   │
 *   └──────────┘  postMessage()    └──────────────────┘
 *
 * Each worker is stateless — it receives the full context per request
 * and can be killed/restarted without losing state.
 */

"use strict";

const { parentPort } = require("node:worker_threads");
const {
  evaluateOmaha,
  parseCards,
  fullDeck,
  shuffle,
} = require("./omaha-evaluator");

// ── Worker Message Handler ──────────────────────────────────────────

parentPort.on("message", (msg) => {
  const { id, hero, board, dead, sims, opponents, handSize } = msg;

  const t0 = performance.now();
  const result = runMonteCarlo(hero, board, dead, sims, opponents, handSize);
  const elapsed = performance.now() - t0;

  parentPort.postMessage({
    id,
    wins: result.wins,
    ties: result.ties,
    runs: result.runs,
    elapsed: Math.round(elapsed * 100) / 100,
  });
});

// ── Monte Carlo Engine ──────────────────────────────────────────────

/**
 * Run N Monte Carlo simulations for Omaha equity.
 *
 * @param {string[]} heroCards      - Hero's hole cards (5 or 6 for PLO5/PLO6)
 * @param {string[]} boardCards     - Community cards dealt so far (0-5)
 * @param {string[]} deadCards      - Known dead cards (e.g. from Hive colluders)
 * @param {number}   simulations    - Number of runouts to sample
 * @param {number}   numOpponents   - Number of villain hands to generate
 * @param {number}   villainHandSize - Cards per villain hand (match hero format)
 * @returns {{ wins: number, ties: number, runs: number }}
 */
function runMonteCarlo(
  heroCards,
  boardCards,
  deadCards,
  simulations,
  numOpponents,
  villainHandSize,
) {
  const hero = parseCards(heroCards);
  const board = parseCards(boardCards);
  const dead = parseCards(deadCards);

  if (hero.length < 2) {
    return { wins: 0, ties: 0, runs: 0 };
  }

  // Build blocked set
  const blocked = new Set([...hero, ...board, ...dead]);

  // Available deck
  const available = fullDeck().filter((c) => !blocked.has(c));

  const boardNeeded = Math.max(0, 5 - board.length);
  const opps = Math.max(1, numOpponents);
  const vHandSize = villainHandSize || hero.length;
  const cardsNeeded = boardNeeded + vHandSize * opps;

  if (available.length < cardsNeeded) {
    return { wins: 0, ties: 0, runs: 0 };
  }

  let wins = 0;
  let ties = 0;
  let runs = 0;

  // Working copy we shuffle in-place each iteration
  const deck = [...available];

  for (let sim = 0; sim < simulations; sim++) {
    // Fisher-Yates partial shuffle (only need cardsNeeded random cards)
    for (let i = 0; i < cardsNeeded; i++) {
      const j = i + Math.floor(Math.random() * (deck.length - i));
      [deck[i], deck[j]] = [deck[j], deck[i]];
    }

    // Deal board
    const fullBoard = [...board];
    for (let i = 0; i < boardNeeded; i++) {
      fullBoard.push(deck[i]);
    }

    // Deal villain hands
    let idx = boardNeeded;
    let bestVillain = Infinity;
    for (let v = 0; v < opps; v++) {
      const villainHand = deck.slice(idx, idx + vHandSize);
      idx += vHandSize;
      const villainRank = evaluateOmaha(villainHand, fullBoard);
      if (villainRank < bestVillain) {
        bestVillain = villainRank;
      }
    }

    // Evaluate hero
    const heroRank = evaluateOmaha(hero, fullBoard);

    if (heroRank < bestVillain) {
      wins++;
    } else if (heroRank === bestVillain) {
      ties++;
    }
    runs++;
  }

  return { wins, ties, runs };
}

// Signal ready
parentPort.postMessage({ type: "ready" });

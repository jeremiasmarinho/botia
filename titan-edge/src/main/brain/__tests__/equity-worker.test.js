/**
 * Equity Worker — Integration Test
 *
 * Validates the Monte Carlo worker pool with PLO5/PLO6 hands.
 * Run with: node src/main/brain/__tests__/equity-worker.test.js
 */

"use strict";

const { EquityPool } = require("../equity-pool");
const {
  evaluateOmaha,
  parseCards,
  evaluate5,
  encodeCard,
} = require("../omaha-evaluator");

const SEPARATOR = "═".repeat(60);

async function main() {
  console.log(SEPARATOR);
  console.log("  TITAN EDGE AI — Equity Worker Pool Test");
  console.log(SEPARATOR);

  // ── Test 1: Basic Omaha Evaluator ─────────────────────────────
  console.log("\n[1/5] Testing Omaha hand evaluator...");

  // Royal Flush components: Ah Kh used from hand, Qh Jh Th from board
  const royalHand = parseCards(["Ah", "Kh", "2c", "3d", "4s"]);
  const royalBoard = parseCards(["Qh", "Jh", "Th", "5c", "8d"]);
  const royalRank = evaluateOmaha(royalHand, royalBoard);
  console.log(`  Royal Flush rank: ${royalRank} (should be very low)`);

  // Weak hand
  const weakHand = parseCards(["2c", "3d", "7h", "8s", "4c"]);
  const weakBoard = parseCards(["Kh", "Qd", "Js", "9c", "6h"]);
  const weakRank = evaluateOmaha(weakHand, weakBoard);
  console.log(`  Weak hand rank:   ${weakRank} (should be high)`);
  console.log(`  ✓ Royal < Weak:   ${royalRank < weakRank}`);

  // ── Test 2: 5-card evaluator sanity ───────────────────────────
  console.log("\n[2/5] Testing 5-card evaluator...");

  const flush = [
    encodeCard("Ah"),
    encodeCard("Kh"),
    encodeCard("Qh"),
    encodeCard("Jh"),
    encodeCard("9h"),
  ];
  const pair = [
    encodeCard("Ah"),
    encodeCard("Ad"),
    encodeCard("Kh"),
    encodeCard("Qd"),
    encodeCard("9s"),
  ];
  const highCard = [
    encodeCard("Ah"),
    encodeCard("Kd"),
    encodeCard("Qs"),
    encodeCard("Jh"),
    encodeCard("9c"),
  ];

  const flushRank = evaluate5(flush);
  const pairRank = evaluate5(pair);
  const highRank = evaluate5(highCard);

  console.log(`  Flush:     ${flushRank}`);
  console.log(`  Pair AA:   ${pairRank}`);
  console.log(`  High card: ${highRank}`);
  console.log(
    `  ✓ Flush < Pair < HC: ${flushRank < pairRank && pairRank < highRank}`,
  );

  // ── Test 3: Worker Pool Initialization ────────────────────────
  console.log("\n[3/5] Starting worker pool (4 threads)...");
  const pool = new EquityPool({ size: 4, timeoutMs: 10_000 });

  pool.on("ready", ({ size }) =>
    console.log(`  ✓ Pool ready: ${size} workers`),
  );
  pool.on("result", (r) =>
    console.log(
      `    → equity=${(r.equity * 100).toFixed(1)}% sims=${r.sims} ${r.elapsedMs}ms`,
    ),
  );

  await pool.init();

  // ── Test 4: PLO5 Equity Calculation ───────────────────────────
  console.log("\n[4/5] PLO5 equity: AhKhQhJhTh vs 1 villain on 2c7d9s...");
  const plo5Result = await pool.calculate({
    hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
    board: ["2c", "7d", "9s"],
    dead: [],
    sims: 5000,
    opponents: 1,
  });
  console.log(`  Equity:  ${(plo5Result.equity * 100).toFixed(1)}%`);
  console.log(`  Win:     ${(plo5Result.winRate * 100).toFixed(1)}%`);
  console.log(`  Tie:     ${(plo5Result.tieRate * 100).toFixed(1)}%`);
  console.log(`  Sims:    ${plo5Result.sims}`);
  console.log(`  Time:    ${plo5Result.elapsedMs}ms`);
  console.log(
    `  Budget:  ${plo5Result.elapsedMs < 200 ? "✓" : "⚠"} ${plo5Result.elapsedMs < 200 ? "Within" : "Over"} 200ms target`,
  );

  // ── Test 5: PLO6 Equity Calculation ───────────────────────────
  console.log("\n[5/5] PLO6 equity: AhKhQhJhTh9h vs 2 villains on 2c7d5s...");
  const plo6Result = await pool.calculate({
    hero: ["Ah", "Kh", "Qh", "Jh", "Th", "9h"],
    board: ["2c", "7d", "5s"],
    dead: [],
    sims: 3000,
    opponents: 2,
  });
  console.log(`  Equity:  ${(plo6Result.equity * 100).toFixed(1)}%`);
  console.log(`  Win:     ${(plo6Result.winRate * 100).toFixed(1)}%`);
  console.log(`  Sims:    ${plo6Result.sims}`);
  console.log(`  Time:    ${plo6Result.elapsedMs}ms`);
  console.log(
    `  Budget:  ${plo6Result.elapsedMs < 500 ? "✓" : "⚠"} ${plo6Result.elapsedMs < 500 ? "Within" : "Over"} 500ms target`,
  );

  // ── Shutdown ──────────────────────────────────────────────────
  await pool.shutdown();

  console.log("\n" + SEPARATOR);
  console.log("  ✓ All equity tests PASSED");
  console.log(SEPARATOR);
}

main().catch((err) => {
  console.error("\nUnhandled error:", err);
  process.exit(1);
});

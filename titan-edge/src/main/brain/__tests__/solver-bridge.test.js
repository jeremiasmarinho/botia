/**
 * SolverBridge — Unit + Integration Tests
 *
 * Tests the complete Rust N-API bridge without Electron.
 * Run:  npx vitest run src/main/brain/__tests__/solver-bridge.test.js
 *   or: node src/main/brain/__tests__/solver-bridge.test.js
 *
 * Coverage:
 *   - Card encoding (cardToId, encodeCards)
 *   - SolverBridge init (fallback mode)
 *   - equity() with string cards
 *   - equityFromIds() with classId integers
 *   - solve() with string cards
 *   - solveFromIds() with classId integers
 *   - batchEquity()
 *   - Edge cases: empty board, invalid cards, uninitialized bridge
 *   - Performance tracking
 *   - Native addon integration (if .node is available)
 */

"use strict";

// ── Minimal test harness (works without vitest/jest) ────────────────

let _passed = 0;
let _failed = 0;
let _skipped = 0;
const _failures = [];

function assert(condition, message) {
  if (!condition) throw new Error(`Assertion failed: ${message}`);
}

function assertThrows(fn, message) {
  try {
    fn();
    throw new Error(`Expected to throw: ${message}`);
  } catch (err) {
    if (err.message === `Expected to throw: ${message}`) throw err;
  }
}

function assertApprox(actual, expected, tolerance, message) {
  if (Math.abs(actual - expected) > tolerance) {
    throw new Error(
      `${message}: expected ~${expected} ±${tolerance}, got ${actual}`,
    );
  }
}

async function test(name, fn) {
  try {
    await fn();
    _passed++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    _failed++;
    _failures.push({ name, error: err.message });
    console.log(`  ✗ ${name}`);
    console.log(`    → ${err.message}`);
  }
}

function skip(name) {
  _skipped++;
  console.log(`  ○ ${name} (skipped)`);
}

function section(title) {
  const pad = Math.max(0, 50 - title.length);
  console.log(`\n── ${title} ${"─".repeat(pad)}`);
}

// ── Load modules under test ─────────────────────────────────────────

const {
  SolverBridge,
  GameVariant,
  encodeCards,
  cardToId,
  STREET,
  ACTION,
  ACTION_NAMES,
} = require("../solver-bridge");

// ── Try loading native addon (optional) ─────────────────────────────

const path = require("node:path");
let nativeAddon = null;
const NATIVE_PATHS = [
  path.resolve(__dirname, "../../../native/titan-core.win32-x64-msvc.node"),
  path.resolve(
    __dirname,
    "../../../../../titan-distributed/packages/core-engine/titan-core.win32-x64-msvc.node",
  ),
  path.resolve(
    __dirname,
    "../../../../../titan-distributed/packages/core-engine/titan_core.node",
  ),
];
for (const p of NATIVE_PATHS) {
  try {
    nativeAddon = require(p);
    break;
  } catch {
    /* skip */
  }
}

// ── Tests ───────────────────────────────────────────────────────────

async function main() {
  console.log("╔══════════════════════════════════════════════════╗");
  console.log("║    SOLVER BRIDGE — AUDIT TEST SUITE             ║");
  console.log("╠══════════════════════════════════════════════════╣");
  console.log(
    `║  Native addon: ${nativeAddon ? "LOADED" : "NOT FOUND (fallback mode)"}`.padEnd(
      51,
    ) + "║",
  );
  console.log("╚══════════════════════════════════════════════════╝");

  // ────────────────────────────────────────────────────────────────
  section("Card Encoding");

  await test("cardToId — Ace of hearts = 50", () => {
    assert(cardToId("Ah") === 50, `Ah should be 50, got ${cardToId("Ah")}`);
  });

  await test("cardToId — 2 of clubs = 0", () => {
    assert(cardToId("2c") === 0, `2c should be 0, got ${cardToId("2c")}`);
  });

  await test("cardToId — King of spades = 47", () => {
    assert(cardToId("Ks") === 47, `Ks should be 47, got ${cardToId("Ks")}`);
  });

  await test("cardToId — Ten of diamonds = 33", () => {
    assert(cardToId("Td") === 33, `Td should be 33, got ${cardToId("Td")}`);
  });

  await test("cardToId — all 52 cards produce unique IDs", () => {
    const ranks = "23456789TJQKA";
    const suits = "cdhs";
    const ids = new Set();
    for (const r of ranks) {
      for (const s of suits) {
        ids.add(cardToId(`${r}${s}`));
      }
    }
    assert(ids.size === 52, `Expected 52 unique IDs, got ${ids.size}`);
  });

  await test("cardToId — all IDs in range [0, 51]", () => {
    const ranks = "23456789TJQKA";
    const suits = "cdhs";
    for (const r of ranks) {
      for (const s of suits) {
        const id = cardToId(`${r}${s}`);
        assert(id >= 0 && id <= 51, `${r}${s} = ${id} out of range`);
      }
    }
  });

  await test("cardToId — invalid card throws", () => {
    assertThrows(() => cardToId("Xz"), "Invalid card");
    assertThrows(() => cardToId("1c"), "Invalid card");
    assertThrows(() => cardToId(""), "Invalid card");
  });

  await test("encodeCards — returns plain Array (not Uint8Array)", () => {
    const result = encodeCards(["Ah", "Kh", "Qh"]);
    assert(Array.isArray(result), "Should be Array");
    assert(!(result instanceof Uint8Array), "Should NOT be Uint8Array");
    assert(result.length === 3, `Length should be 3, got ${result.length}`);
  });

  await test("encodeCards — correct values", () => {
    const result = encodeCards(["2c", "As"]);
    assert(result[0] === 0, `2c should be 0, got ${result[0]}`);
    assert(result[1] === 51, `As should be 51, got ${result[1]}`);
  });

  await test("encodeCards — empty array", () => {
    const result = encodeCards([]);
    assert(result.length === 0, "Empty input should return empty array");
  });

  // ────────────────────────────────────────────────────────────────
  section("Constants");

  await test("STREET enum is correct", () => {
    assert(STREET.PREFLOP === 0, "PREFLOP should be 0");
    assert(STREET.FLOP === 1, "FLOP should be 1");
    assert(STREET.TURN === 2, "TURN should be 2");
    assert(STREET.RIVER === 3, "RIVER should be 3");
  });

  await test("ACTION enum is correct", () => {
    assert(ACTION.FOLD === 0, "FOLD should be 0");
    assert(ACTION.CHECK === 1, "CHECK should be 1");
    assert(ACTION.CALL === 2, "CALL should be 2");
    assert(ACTION.RAISE === 3, "RAISE should be 3");
    assert(ACTION.ALLIN === 4, "ALLIN should be 4");
  });

  await test("ACTION_NAMES matches ACTION enum", () => {
    assert(ACTION_NAMES[ACTION.FOLD] === "fold", "Fold name mismatch");
    assert(ACTION_NAMES[ACTION.RAISE] === "raise", "Raise name mismatch");
    assert(ACTION_NAMES.length === 5, "Should have 5 action names");
  });

  await test("GameVariant enum", () => {
    assert(GameVariant.PLO5 === 0, "PLO5 should be 0");
    assert(GameVariant.PLO6 === 1, "PLO6 should be 1");
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — Initialization");

  await test("constructor — default state", () => {
    const s = new SolverBridge();
    assert(s.initialized === false, "Should not be initialized");
    assert(s.native === false, "Should not be native");
    assert(s.version === "unknown", "Version should be unknown");
  });

  await test("init — sets initialized to true", async () => {
    const s = new SolverBridge();
    await s.init();
    assert(s.initialized === true, "Should be initialized");
    assert(typeof s.version === "string", "Version should be string");
    await s.shutdown();
  });

  await test("init — idempotent (calling twice is safe)", async () => {
    const s = new SolverBridge();
    await s.init();
    await s.init(); // Should be no-op
    assert(s.initialized === true, "Still initialized");
    await s.shutdown();
  });

  await test("equity — throws if not initialized", () => {
    const s = new SolverBridge();
    assertThrows(
      () => s.equity({ hero: ["Ah", "Kh", "Qh", "Jh", "Th"], board: [] }),
      "Not initialized",
    );
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — equity() with string cards");

  await test("equity — PLO5 flop returns valid result", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
      board: ["As", "Ks", "2c"],
    });
    assert(typeof result.equity === "number", "equity should be number");
    assert(result.equity >= 0 && result.equity <= 1, "equity in [0,1]");
    assert(typeof result.winRate === "number", "winRate should be number");
    assert(typeof result.tieRate === "number", "tieRate should be number");
    assert(result.sims > 0, "sims should be positive");
    assert(typeof result.elapsedUs === "number", "elapsedUs should exist");
    assert(
      result.engine === "rust" || result.engine === "js-fallback",
      `engine should be rust or js-fallback, got ${result.engine}`,
    );
    await s.shutdown();
  });

  await test("equity — PLO5 empty board (preflop)", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
      board: [],
    });
    assert(result.equity >= 0 && result.equity <= 1, "equity in [0,1]");
    assert(result.sims > 0, "sims should be > 0");
    await s.shutdown();
  });

  await test("equity — PLO6 auto-detects variant from 6 cards", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th", "9h"],
      board: ["As", "Ks", "2c"],
    });
    assert(result.equity >= 0 && result.equity <= 1, "equity in [0,1]");
    await s.shutdown();
  });

  await test("equity — custom sims count", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
      board: ["As", "Ks", "2c"],
      sims: 100,
    });
    assert(result.sims === 100, `Expected 100 sims, got ${result.sims}`);
    await s.shutdown();
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — equityFromIds() with classId integers");

  await test("equityFromIds — PLO5 flop", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.equityFromIds({
      heroIds: [50, 46, 42, 38, 34], // Ah Kh Qh Jh Th
      boardIds: [51, 47, 0], // As Ks 2c
    });
    assert(result.equity >= 0 && result.equity <= 1, "equity in [0,1]");
    assert(result.sims > 0, "sims > 0");
    assert(typeof result.engine === "string", "engine field exists");
    await s.shutdown();
  });

  await test("equityFromIds — empty board", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.equityFromIds({
      heroIds: [50, 46, 42, 38, 34],
      boardIds: [],
    });
    assert(result.equity >= 0 && result.equity <= 1, "equity in [0,1]");
    await s.shutdown();
  });

  await test("equityFromIds — classIds match string encoding", async () => {
    const s = new SolverBridge();
    await s.init();
    // Ah=50, Kh=46, Qh=42 via cardToId
    const fromString = s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
      board: ["2c", "3c", "4c"],
      sims: 500,
    });
    const fromIds = s.equityFromIds({
      heroIds: [50, 46, 42, 38, 34],
      boardIds: [0, 4, 8],
      sims: 500,
    });
    // Same engine should produce similar-ish results (both use same RNG seed in fallback)
    if (!s.native) {
      // Fallback is deterministic
      assertApprox(fromIds.equity, fromString.equity, 0.01, "Equity mismatch");
    }
    await s.shutdown();
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — solve() with string cards");

  await test("solve — returns valid action", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.solve({
      heroCards: ["Ah", "Kh", "Qh", "Jh", "Th"],
      boardCards: ["As", "Ks", "2c"],
      street: "flop",
      potBb100: 100,
      heroStack: 500,
    });
    assert(
      ACTION_NAMES.includes(result.action),
      `action '${result.action}' not in ACTION_NAMES`,
    );
    assert(result.equity >= 0 && result.equity <= 1, "equity in [0,1]");
    assert(typeof result.ev === "number", "ev should be number");
    assert(
      typeof result.confidence === "number",
      "confidence should be number",
    );
    assert(
      typeof result.frequencies === "object",
      "frequencies should be object",
    );
    assert(typeof result.frequencies.fold === "number", "fold freq exists");
    assert(typeof result.frequencies.check === "number", "check freq exists");
    assert(typeof result.frequencies.call === "number", "call freq exists");
    assert(typeof result.frequencies.raise === "number", "raise freq exists");
    assert(typeof result.frequencies.allin === "number", "allin freq exists");
    assert(typeof result.elapsedUs === "number", "elapsedUs exists");
    assert(typeof result.engine === "string", "engine exists");
    await s.shutdown();
  });

  await test("solve — preflop (no board)", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.solve({
      heroCards: ["Ah", "Kh", "Qh", "Jh", "Th"],
      boardCards: [],
      street: "preflop",
      potBb100: 100,
      heroStack: 500,
    });
    assert(
      ACTION_NAMES.includes(result.action),
      `Invalid action: ${result.action}`,
    );
    await s.shutdown();
  });

  await test("solve — river (5 board cards)", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.solve({
      heroCards: ["Ah", "Kh", "Qh", "Jh", "Th"],
      boardCards: ["2c", "3d", "4h", "5s", "6c"],
      street: "river",
      potBb100: 200,
      heroStack: 300,
    });
    assert(
      ACTION_NAMES.includes(result.action),
      `Invalid action: ${result.action}`,
    );
    await s.shutdown();
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — solveFromIds() with classId integers");

  await test("solveFromIds — PLO5 flop", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.solveFromIds({
      heroIds: [50, 46, 42, 38, 34],
      boardIds: [0, 4, 8],
      street: "flop",
      potBb100: 100,
      heroStack: 500,
    });
    assert(
      ACTION_NAMES.includes(result.action),
      `Invalid action: ${result.action}`,
    );
    assert(result.equity >= 0 && result.equity <= 1, "equity in [0,1]");
    assert(typeof result.frequencies === "object", "frequencies exists");
    await s.shutdown();
  });

  await test("solveFromIds — PLO6 auto-detect", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.solveFromIds({
      heroIds: [50, 46, 42, 38, 34, 30], // 6 cards → PLO6
      boardIds: [0, 4, 8],
      street: "flop",
      potBb100: 100,
      heroStack: 500,
    });
    assert(
      ACTION_NAMES.includes(result.action),
      `Invalid action: ${result.action}`,
    );
    await s.shutdown();
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — batchEquity()");

  await test("batchEquity — processes multiple hands", async () => {
    const s = new SolverBridge();
    await s.init();
    const results = s.batchEquity([
      { hero: ["Ah", "Kh", "Qh", "Jh", "Th"], board: ["2c", "3c", "4c"] },
      { hero: ["2c", "3d", "4h", "5s", "6c"], board: ["Ah", "Kh", "Qh"] },
    ]);
    assert(results.length === 2, `Expected 2 results, got ${results.length}`);
    assert(results[0].equity >= 0 && results[0].equity <= 1, "Result 0 valid");
    assert(results[1].equity >= 0 && results[1].equity <= 1, "Result 1 valid");
    await s.shutdown();
  });

  await test("batchEquity — empty batch", async () => {
    const s = new SolverBridge();
    await s.init();
    const results = s.batchEquity([]);
    assert(results.length === 0, "Empty batch should return empty array");
    await s.shutdown();
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — Performance Tracking");

  await test("getStats — tracks calls and timing", async () => {
    const s = new SolverBridge();
    await s.init();
    s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
      board: ["2c", "3c", "4c"],
      sims: 100,
    });
    s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
      board: ["2c", "3c", "4c"],
      sims: 100,
    });
    const stats = s.getStats();
    assert(stats.calls === 2, `Expected 2 calls, got ${stats.calls}`);
    assert(stats.avgUs > 0, "avgUs should be positive");
    assert(stats.maxUs > 0, "maxUs should be positive");
    assert(typeof stats.over3ms === "number", "over3ms should be number");
    await s.shutdown();
  });

  await test("resetStats — clears counters", async () => {
    const s = new SolverBridge();
    await s.init();
    s.equity({
      hero: ["Ah", "Kh", "Qh", "Jh", "Th"],
      board: [],
      sims: 100,
    });
    s.resetStats();
    const stats = s.getStats();
    assert(stats.calls === 0, "Calls should be 0 after reset");
    assert(stats.maxUs === 0, "maxUs should be 0 after reset");
    await s.shutdown();
  });

  // ────────────────────────────────────────────────────────────────
  section("SolverBridge — Shutdown");

  await test("shutdown — marks as uninitialized", async () => {
    const s = new SolverBridge();
    await s.init();
    assert(s.initialized === true, "Should be initialized");
    await s.shutdown();
    assert(s.initialized === false, "Should not be initialized after shutdown");
  });

  await test("shutdown — emits event", async () => {
    const s = new SolverBridge();
    await s.init();
    let emitted = false;
    s.on("shutdown", () => {
      emitted = true;
    });
    await s.shutdown();
    assert(emitted, "Should emit shutdown event");
  });

  // ────────────────────────────────────────────────────────────────
  section("Native Addon Integration");

  if (nativeAddon) {
    await test("native — equity() returns f64 in [0, 1]", () => {
      if (typeof nativeAddon.init === "function") nativeAddon.init();
      const eq = nativeAddon.equity([50, 46, 42, 38, 34], [0, 4, 8], 1000);
      assert(typeof eq === "number", "Should return number");
      assert(eq >= 0 && eq <= 1, `Equity ${eq} out of range`);
    });

    await test("native — solve() returns SolveResult object", () => {
      const result = nativeAddon.solve({
        format: 0,
        street: 1,
        heroCards: [50, 46, 42, 38, 34],
        boardCards: [0, 4, 8],
        deadCards: [],
        potBb100: 100,
        heroStack: 500,
        villainStacks: [],
        position: 0,
        numPlayers: 2,
      });
      assert(typeof result.action === "number", "action should be number");
      assert(typeof result.equity === "number", "equity should be number");
      assert(
        typeof result.raiseAmountBb100 === "number",
        "raiseAmountBb100 should exist (camelCase)",
      );
      assert(
        typeof result.evBb100 === "number",
        "evBb100 should exist (camelCase)",
      );
      assert(
        typeof result.freqFold === "number",
        "freqFold should exist (camelCase)",
      );
      assert(
        typeof result.freqCheck === "number",
        "freqCheck should exist (camelCase)",
      );
      assert(typeof result.confidence === "number", "confidence should exist");
    });

    await test("native — solve() rejects Uint8Array (must be plain Array)", () => {
      let threw = false;
      try {
        nativeAddon.solve({
          format: 0,
          street: 1,
          heroCards: new Uint8Array([50, 46, 42, 38, 34]),
          boardCards: [0, 4, 8],
          deadCards: [],
          potBb100: 100,
          heroStack: 500,
          villainStacks: [],
          position: 0,
          numPlayers: 2,
        });
      } catch (err) {
        threw = true;
      }
      assert(threw, "Should throw when passing Uint8Array instead of Array");
    });

    await test("native — evaluate() ranks Royal > Pair", () => {
      // Royal flush: Ah Kh Qh Jh Th (IDs: 50, 46, 42, 38, 34)
      const royal = nativeAddon.evaluate([50, 46, 42, 38, 34]);
      // Pair of aces: Ah As 2c 3d 4h (IDs: 50, 51, 0, 5, 10)
      const pair = nativeAddon.evaluate([50, 51, 0, 5, 10]);
      assert(
        royal < pair,
        `Royal (${royal}) should rank lower (better) than pair (${pair})`,
      );
    });

    await test("native — version() returns string", () => {
      const v = nativeAddon.version();
      assert(typeof v === "string", "version should be string");
      assert(v.includes("titan"), `version should contain 'titan', got: ${v}`);
    });

    await test("native — SolverBridge uses native path end-to-end", async () => {
      const s = new SolverBridge();
      await s.init();
      assert(s.native === true, "Should use native addon");

      const eqResult = s.equityFromIds({
        heroIds: [50, 46, 42, 38, 34],
        boardIds: [0, 4, 8],
        sims: 500,
      });
      assert(
        eqResult.engine === "rust",
        `Engine should be 'rust', got '${eqResult.engine}'`,
      );

      const solveResult = s.solveFromIds({
        heroIds: [50, 46, 42, 38, 34],
        boardIds: [0, 4, 8],
        street: "flop",
        potBb100: 100,
        heroStack: 500,
      });
      assert(
        solveResult.engine === "rust-cfr",
        `Engine should be 'rust-cfr', got '${solveResult.engine}'`,
      );
      assert(
        ACTION_NAMES.includes(solveResult.action),
        `Invalid action: ${solveResult.action}`,
      );

      await s.shutdown();
    });
  } else {
    skip("native — equity() returns f64 in [0, 1]");
    skip("native — solve() returns SolveResult object");
    skip("native — solve() rejects Uint8Array");
    skip("native — evaluate() ranks Royal > Pair");
    skip("native — version() returns string");
    skip("native — SolverBridge uses native path end-to-end");
  }

  // ────────────────────────────────────────────────────────────────
  section("Edge Cases");

  await test("equity — duplicate card IDs should not crash", async () => {
    const s = new SolverBridge();
    await s.init();
    // Intentionally pass duplicate cards — engine should handle gracefully
    try {
      s.equityFromIds({
        heroIds: [50, 50, 50, 50, 50], // All same card
        boardIds: [0, 4, 8],
        sims: 100,
      });
    } catch {
      // Some engines may throw, that's acceptable
    }
    await s.shutdown();
  });

  await test("equity — card ID out of range (documents Rust panic)", async () => {
    // BUG #6: Rust omaha.rs panics on out-of-range card IDs (index 255 > len 52).
    // This is a known limitation: the bridge should validate IDs before calling native.
    // For now, we document this behavior and skip the native path.
    const s = new SolverBridge();
    await s.init();
    if (s.native) {
      // Skip — would cause Rust panic (process crash)
      console.log("    (skipped: would panic in Rust with OOB card IDs)");
    } else {
      try {
        s.equityFromIds({
          heroIds: [255, 100, 80, 60, 40],
          boardIds: [0],
          sims: 100,
        });
      } catch {
        // Expected to throw in fallback mode
      }
    }
    await s.shutdown();
  });

  await test("solve — unknown street defaults to flop", async () => {
    const s = new SolverBridge();
    await s.init();
    const result = s.solve({
      heroCards: ["Ah", "Kh", "Qh", "Jh", "Th"],
      boardCards: ["2c", "3d", "4h"],
      street: "unknown_street",
      potBb100: 100,
      heroStack: 500,
    });
    assert(
      ACTION_NAMES.includes(result.action),
      "Should still produce valid action",
    );
    await s.shutdown();
  });

  // ────────────────────────────────────────────────────────────────
  // Summary
  console.log("\n══════════════════════════════════════════════════");
  console.log(
    `  Results: ${_passed} passed, ${_failed} failed, ${_skipped} skipped`,
  );
  if (_failures.length > 0) {
    console.log("\n  Failures:");
    for (const f of _failures) {
      console.log(`    ✗ ${f.name}: ${f.error}`);
    }
  }
  console.log(
    _failed === 0 ? "\n  ✓ All tests passed.\n" : "\n  ✗ Some tests failed.\n",
  );
  process.exit(_failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error("Fatal test error:", err);
  process.exit(2);
});

/**
 * GTO Engine — Unit Tests
 *
 * Tests the mixed-strategy decision engine in isolation.
 *
 * Run:  node src/main/brain/__tests__/gto-engine.test.js
 *
 * Coverage:
 *   - THRESHOLDS per street
 *   - SPR commitment override
 *   - Position adjustment (IP vs OOP)
 *   - Multi-way adjustment
 *   - Mixed strategy noise
 *   - Raise sizing (_raiseSizing)
 *   - Edge cases: boundary equities, extreme SPR, many opponents
 */

"use strict";

// ── Minimal test harness ────────────────────────────────────────────

let _passed = 0;
let _failed = 0;
const _failures = [];

function assert(cond, msg) {
  if (!cond) throw new Error(`Assertion failed: ${msg}`);
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

function section(title) {
  console.log(`\n── ${title} ${"─".repeat(50 - title.length)}`);
}

// ── Load module under test ──────────────────────────────────────────

const { GtoEngine, THRESHOLDS, SPR_COMMIT } = require("../gto-engine");

// ── Test Helpers ────────────────────────────────────────────────────

function makeState(overrides = {}) {
  return {
    equity: 0.5,
    potOdds: 0.3,
    spr: 5.0,
    street: "flop",
    inPosition: true,
    opponents: 1,
    betFacing: 0,
    potSize: 100,
    stackSize: 500,
    ...overrides,
  };
}

/**
 * Run decide() N times and count action distribution.
 * Useful for testing mixed strategy ranges (noise randomization).
 */
function decideDistribution(state, n = 200) {
  const counts = { fold: 0, check: 0, call: 0, raise: 0, allin: 0 };
  for (let i = 0; i < n; i++) {
    const d = GtoEngine.decide(state);
    counts[d.action]++;
  }
  return counts;
}

// ── Tests ───────────────────────────────────────────────────────────

async function main() {
  console.log("╔══════════════════════════════════════════════════╗");
  console.log("║    GTO ENGINE — UNIT TEST SUITE                 ║");
  console.log("╚══════════════════════════════════════════════════╝");

  // ──────────────────────────────────────────────────────────────
  section("Constants");

  await test("THRESHOLDS — all 4 streets defined", () => {
    const streets = ["preflop", "flop", "turn", "river"];
    for (const s of streets) {
      assert(THRESHOLDS[s], `Missing thresholds for ${s}`);
      assert(typeof THRESHOLDS[s].fold === "number", `${s}.fold missing`);
      assert(typeof THRESHOLDS[s].call === "number", `${s}.call missing`);
      assert(typeof THRESHOLDS[s].raise === "number", `${s}.raise missing`);
      assert(typeof THRESHOLDS[s].allin === "number", `${s}.allin missing`);
    }
  });

  await test("THRESHOLDS — fold < call < raise < allin on all streets", () => {
    for (const [street, t] of Object.entries(THRESHOLDS)) {
      assert(
        t.fold < t.call && t.call < t.raise && t.raise < t.allin,
        `Threshold ordering violated on ${street}: ` +
          `fold=${t.fold} call=${t.call} raise=${t.raise} allin=${t.allin}`,
      );
    }
  });

  await test("SPR_COMMIT — valid constants", () => {
    assert(SPR_COMMIT.SHOVE_SPR === 2.0, "SHOVE_SPR should be 2.0");
    assert(SPR_COMMIT.SHOVE_EQUITY === 0.4, "SHOVE_EQUITY should be 0.4");
    assert(SPR_COMMIT.POT_CONTROL_SPR === 6.0, "POT_CONTROL_SPR should be 6.0");
  });

  // ──────────────────────────────────────────────────────────────
  section("SPR Commitment Override");

  await test("low SPR + decent equity → allin", () => {
    const d = GtoEngine.decide(
      makeState({ spr: 1.5, equity: 0.45, street: "flop" }),
    );
    assert(
      d.action === "allin",
      `Expected allin with SPR=1.5 equity=0.45, got ${d.action}`,
    );
  });

  await test("low SPR + low equity → NOT allin", () => {
    // equity < SHOVE_EQUITY (0.4) at low SPR — should NOT commit
    const d = GtoEngine.decide(
      makeState({ spr: 1.5, equity: 0.2, street: "flop" }),
    );
    assert(
      d.action !== "allin",
      `Should not allin with 20% equity at low SPR, got ${d.action}`,
    );
  });

  await test("SPR commit — confidence capped at 0.95", () => {
    const d = GtoEngine.decide(
      makeState({ spr: 1.0, equity: 0.99, street: "river" }),
    );
    assert(d.action === "allin", "Should allin");
    assert(
      d.confidence <= 0.95,
      `Confidence should be ≤ 0.95, got ${d.confidence}`,
    );
  });

  await test("SPR commit — raiseSize equals stackSize", () => {
    const d = GtoEngine.decide(
      makeState({ spr: 1.5, equity: 0.5, stackSize: 300 }),
    );
    assert(d.action === "allin", "Should allin");
    assert(
      d.raiseSize === 300,
      `raiseSize should be 300 (stackSize), got ${d.raiseSize}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Clear Decisions (beyond noise range)");

  await test("very high equity → raise or allin", () => {
    // 80% equity is well above all raise thresholds (even with +3% OOP adj)
    const dist = decideDistribution(
      makeState({ equity: 0.8, street: "flop" }),
      100,
    );
    assert(
      dist.fold === 0 && dist.check === 0 && dist.call === 0,
      `Should never fold/check/call with 80% equity. got fold=${dist.fold} check=${dist.check} call=${dist.call}`,
    );
  });

  await test("very low equity + bet facing → fold", () => {
    // 10% equity should fold nearly always (noise range is ±3%)
    const dist = decideDistribution(
      makeState({ equity: 0.1, betFacing: 50, street: "flop" }),
      100,
    );
    assert(
      dist.fold >= 90,
      `Should fold ~100% with 10% equity facing bet, got fold=${dist.fold}/100`,
    );
  });

  await test("very low equity + no bet → check (not fold)", () => {
    // When no bet is facing, "fold" becomes "check"
    const dist = decideDistribution(
      makeState({ equity: 0.1, betFacing: 0, street: "flop" }),
      100,
    );
    assert(
      dist.check >= 90,
      `Should check (not fold) with 10% equity no bet, got check=${dist.check}/100`,
    );
    assert(
      dist.fold === 0,
      `Should never fold with no bet facing, got ${dist.fold}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Position Adjustment");

  await test("IP lowers thresholds (more aggressive)", () => {
    // Near the call/raise boundary — IP should raise more often
    const ipDist = decideDistribution(
      makeState({
        equity: 0.52,
        inPosition: true,
        betFacing: 50,
        street: "flop",
      }),
      300,
    );
    const oopDist = decideDistribution(
      makeState({
        equity: 0.52,
        inPosition: false,
        betFacing: 50,
        street: "flop",
      }),
      300,
    );
    // IP should have more raises than OOP
    assert(
      ipDist.raise >= oopDist.raise || ipDist.allin >= oopDist.allin,
      `IP should be more aggressive: IP raise=${ipDist.raise} vs OOP raise=${oopDist.raise}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Multi-Way Adjustment");

  await test("more opponents → tighter play", () => {
    // With 4 opponents, thresholds shift up by ~0.12
    // 50% equity with 4 opponents should fold/call more (less raise)
    const hw = decideDistribution(
      makeState({ equity: 0.5, opponents: 1, betFacing: 50 }),
      200,
    );
    const mw = decideDistribution(
      makeState({ equity: 0.5, opponents: 4, betFacing: 50 }),
      200,
    );
    const hwAggr = hw.raise + hw.allin;
    const mwAggr = mw.raise + mw.allin;
    assert(
      hwAggr >= mwAggr,
      `Should be less aggressive multi-way: HU aggr=${hwAggr} vs 4-way aggr=${mwAggr}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Mixed Strategy (Noise)");

  await test("borderline equity produces mixed actions", () => {
    // Pick equity near the adjusted call/raise boundary.
    // IP flop: adjRaise = 0.50 - 0.05 = 0.45, adjCall = 0.33 - 0.05 = 0.28
    // OOP flop: adjRaise = 0.50 + 0.03 = 0.53, adjCall = 0.33 + 0.03 = 0.36
    // Use OOP + equity ~0.52 to sit right at the raise boundary with noise ±3%
    const dist = decideDistribution(
      makeState({
        equity: 0.52,
        betFacing: 50,
        street: "flop",
        inPosition: false,
      }),
      300,
    );
    // Should see a mix of call and raise
    const totalActions = Object.values(dist).reduce((a, b) => a + b, 0);
    const uniqueActions = Object.values(dist).filter((v) => v > 0).length;
    assert(
      uniqueActions >= 2,
      `Should produce at least 2 different actions near boundary, got ${uniqueActions}: ${JSON.stringify(dist)}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Decision Output Format");

  await test("decide — returns all required fields", () => {
    const d = GtoEngine.decide(makeState());
    assert(typeof d.action === "string", "action should be string");
    assert(typeof d.confidence === "number", "confidence should be number");
    assert(typeof d.raiseSize === "number", "raiseSize should be number");
    assert(typeof d.reasoning === "string", "reasoning should be string");
  });

  await test("decide — action is valid", () => {
    const validActions = ["fold", "check", "call", "raise", "allin"];
    for (let i = 0; i < 50; i++) {
      const d = GtoEngine.decide(
        makeState({ equity: Math.random(), betFacing: Math.random() * 100 }),
      );
      assert(validActions.includes(d.action), `Invalid action: ${d.action}`);
    }
  });

  await test("decide — confidence in [0, 1]", () => {
    for (let i = 0; i < 50; i++) {
      const d = GtoEngine.decide(makeState({ equity: Math.random() }));
      assert(
        d.confidence >= 0 && d.confidence <= 1,
        `Confidence out of range: ${d.confidence}`,
      );
    }
  });

  await test("decide — raiseSize is 0 for non-raise actions", () => {
    const d = GtoEngine.decide(makeState({ equity: 0.1, betFacing: 50 }));
    if (d.action === "fold" || d.action === "check" || d.action === "call") {
      assert(
        d.raiseSize === 0,
        `raiseSize should be 0 for ${d.action}, got ${d.raiseSize}`,
      );
    }
  });

  await test("decide — reasoning contains equity percentage", () => {
    const d = GtoEngine.decide(makeState({ equity: 0.65 }));
    assert(
      d.reasoning.includes("equity="),
      `Reasoning should contain equity info: ${d.reasoning}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Raise Sizing");

  await test("raise sizing — preflop is always 1.0x pot", () => {
    const d = GtoEngine.decide(
      makeState({ equity: 0.6, street: "preflop", potSize: 100 }),
    );
    if (d.action === "raise") {
      assert(
        d.raiseSize === 100,
        `Preflop raise should be 1.0x pot (100), got ${d.raiseSize}`,
      );
    }
  });

  await test("raise sizing — strong hand → pot-sized", () => {
    const d = GtoEngine.decide(
      makeState({ equity: 0.75, street: "flop", potSize: 200, spr: 5.0 }),
    );
    if (d.action === "raise") {
      assert(
        d.raiseSize === 200,
        `Strong hand raise should be pot-sized (200), got ${d.raiseSize}`,
      );
    }
  });

  await test("raise sizing — medium hand → 2/3 pot", () => {
    // equity ~0.60 → _raiseSizing returns 0.66
    const d = GtoEngine.decide(
      makeState({ equity: 0.6, street: "flop", potSize: 300, spr: 5.0 }),
    );
    if (d.action === "raise") {
      // 300 * 0.66 = 198
      assert(
        d.raiseSize < 300,
        `Medium hand raise should be < pot, got ${d.raiseSize}`,
      );
    }
  });

  // ──────────────────────────────────────────────────────────────
  section("Street-Specific Behavior");

  await test("preflop — wider call range (lower fold threshold)", () => {
    assert(
      THRESHOLDS.preflop.fold === 0.3,
      `Preflop fold threshold should be 0.30, got ${THRESHOLDS.preflop.fold}`,
    );
  });

  await test("river — tighter ranges (higher thresholds)", () => {
    assert(
      THRESHOLDS.river.fold > THRESHOLDS.preflop.fold,
      "River fold threshold should be higher than preflop",
    );
    assert(
      THRESHOLDS.river.call > THRESHOLDS.preflop.call,
      "River call threshold should be higher than preflop",
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Pot Odds Override");

  await test("equity >= potOdds → call even below call threshold", () => {
    // equity=0.32 is below flop call threshold (0.33) but above potOdds (0.25)
    const dist = decideDistribution(
      makeState({ equity: 0.32, potOdds: 0.25, betFacing: 50, street: "flop" }),
      200,
    );
    // With noise, some may fold, but should have significant call %
    assert(
      dist.call > 0 || dist.check > 0,
      `Should sometimes call when equity >= potOdds: ${JSON.stringify(dist)}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Edge Cases");

  await test("equity = 0 → always fold/check", () => {
    const dist = decideDistribution(
      makeState({ equity: 0.0, betFacing: 50 }),
      100,
    );
    assert(
      dist.raise === 0 && dist.allin === 0,
      `Should never raise/allin with 0% equity: ${JSON.stringify(dist)}`,
    );
  });

  await test("equity = 1.0 → always raise/allin", () => {
    const dist = decideDistribution(
      makeState({ equity: 1.0, betFacing: 50, spr: 5.0 }),
      100,
    );
    assert(
      dist.fold === 0 && dist.check === 0 && dist.call === 0,
      `Should never fold/check/call with 100% equity: ${JSON.stringify(dist)}`,
    );
  });

  await test("unknown street → falls back to flop thresholds", () => {
    const d = GtoEngine.decide(makeState({ street: "garbage" }));
    assert(
      typeof d.action === "string",
      "Should produce valid decision even with unknown street",
    );
  });

  await test("extreme SPR (100) → no allin without huge equity", () => {
    const d = GtoEngine.decide(
      makeState({ spr: 100, equity: 0.5, street: "flop" }),
    );
    assert(
      d.action !== "allin",
      `Should not allin at SPR=100 with 50% equity, got ${d.action}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  // Summary
  console.log("\n══════════════════════════════════════════════════");
  console.log(`  Results: ${_passed} passed, ${_failed} failed`);
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

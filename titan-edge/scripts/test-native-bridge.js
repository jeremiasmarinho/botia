/**
 * test-native-bridge.js — Standalone validation of the Rust N-API bridge
 *
 * Run: node titan-edge/scripts/test-native-bridge.js
 *
 * This script validates the complete data path from JS → Rust → JS
 * without launching Electron, vision, or ADB. It's the fastest way
 * to confirm the .node addon is working after compilation.
 */

"use strict";

const path = require("node:path");
const { performance } = require("node:perf_hooks");

// ── Config ──────────────────────────────────────────────────────────

const SIMS = 5000;
const ITERATIONS = 100; // benchmark iterations
const TARGET_US = 200_000; // target: <200ms generous initial threshold

// ── Load Addon ──────────────────────────────────────────────────────

const PLATFORM_MAP = {
  "win32-x64": "titan-core.win32-x64-msvc.node",
  "linux-x64": "titan-core.linux-x64-gnu.node",
  "darwin-x64": "titan-core.darwin-x64.node",
  "darwin-arm64": "titan-core.darwin-arm64.node",
};

const platformKey = `${process.platform}-${process.arch}`;
const platformFile = PLATFORM_MAP[platformKey] || "titan-core.node";

const searchPaths = [
  path.resolve(__dirname, "../native", platformFile),
  path.resolve(__dirname, "../native/titan_core.node"),
  path.resolve(
    __dirname,
    "../../titan-distributed/packages/core-engine",
    platformFile,
  ),
  path.resolve(
    __dirname,
    "../../titan-distributed/packages/core-engine/titan_core.node",
  ),
];

let addon = null;
for (const p of searchPaths) {
  try {
    addon = require(p);
    console.log(`\n✓ Loaded: ${p}\n`);
    break;
  } catch {
    /* skip */
  }
}

if (!addon) {
  console.error("\n✗ Failed to load titan_core.node from any path:");
  searchPaths.forEach((p) => console.error(`  → ${p}`));
  console.error(
    "\n  Build it: cd titan-distributed/packages/core-engine && npm run build\n",
  );
  process.exit(1);
}

// ── Init ────────────────────────────────────────────────────────────

if (typeof addon.init === "function") {
  addon.init();
}
console.log(
  `Engine: ${typeof addon.version === "function" ? addon.version() : "unknown"}`,
);
console.log(`Platform: ${platformKey}`);
console.log(`─────────────────────────────────────────────\n`);

// ── Test Cases ──────────────────────────────────────────────────────

const tests = [
  {
    name: "PLO5 Flop — Trip Aces",
    hero: [48, 49, 40, 36, 32], // A♣ A♦ Q♣ J♣ T♣
    board: [50, 44, 38], // A♥ K♣ J♥
    expectEquity: [0.3, 0.99], // very strong hand
  },
  {
    name: "PLO5 Turn — Club flush",
    hero: [0, 4, 8, 12, 16], // 2♣ 3♣ 4♣ 5♣ 6♣
    board: [48, 44, 40, 36], // A♣ K♣ Q♣ J♣
    expectEquity: [0.0, 0.99], // made flush in Omaha
  },
  {
    name: "PLO6 Flop — 6 cards",
    hero: [48, 49, 50, 40, 36, 32], // A♣ A♦ A♥ Q♣ J♣ T♣
    board: [51, 44, 38], // A♠ K♣ J♥
    expectEquity: [0.3, 0.99], // quad aces draw/made
  },
  {
    name: "PLO5 Preflop — No board",
    hero: [48, 44, 40, 36, 32],
    board: [],
    expectEquity: [0.1, 0.99],
  },
];

// ── Run Tests ───────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

for (const tc of tests) {
  const heroBuf = Array.from(tc.hero);
  const boardBuf = Array.from(tc.board);

  // Test equity()
  if (typeof addon.equity === "function") {
    const t0 = performance.now();
    const eq = addon.equity(heroBuf, boardBuf, SIMS);
    const us = Math.round((performance.now() - t0) * 1000);

    const inRange = eq >= tc.expectEquity[0] && eq <= tc.expectEquity[1];
    const fast = us < TARGET_US;
    const status = inRange && fast ? "PASS" : "FAIL";

    if (status === "PASS") passed++;
    else failed++;

    console.log(
      `[${status}] equity() — ${tc.name}` +
        `\n       Equity: ${(eq * 100).toFixed(1)}%  (expect ${(tc.expectEquity[0] * 100).toFixed(0)}-${(tc.expectEquity[1] * 100).toFixed(0)}%)` +
        `\n       Time:   ${us}µs  (target <${TARGET_US}µs)` +
        (!fast ? " ⚠ SLOW" : "") +
        "\n",
    );
  }

  // Test solve()
  if (typeof addon.solve === "function" && tc.board.length >= 3) {
    // napi-rs #[napi(object)] auto-converts Rust snake_case → JS camelCase
    const solvePayload = {
      format: tc.hero.length >= 6 ? 1 : 0,
      street: tc.board.length === 3 ? 1 : tc.board.length === 4 ? 2 : 3,
      heroCards: Array.from(tc.hero),
      boardCards: Array.from(tc.board),
      deadCards: [],
      potBb100: 100,
      heroStack: 500,
      villainStacks: [],
      position: 0,
      numPlayers: 2,
    };

    try {
      const t0 = performance.now();
      const result = addon.solve(solvePayload);
      const us = Math.round((performance.now() - t0) * 1000);

      const actions = ["fold", "check", "call", "raise", "allin"];
      const actionName = actions[result.action] || "unknown";
      const fast = us < TARGET_US;

      passed++;
      console.log(
        `[PASS] solve() — ${tc.name}` +
          `\n       Action: ${actionName.toUpperCase()} (equity=${(result.equity * 100).toFixed(1)}%, EV=${result.evBb100})` +
          `\n       Freq: F=${(result.freqFold * 100).toFixed(0)}% K=${(result.freqCheck * 100).toFixed(0)}% C=${(result.freqCall * 100).toFixed(0)}% R=${(result.freqRaise * 100).toFixed(0)}% A=${(result.freqAllin * 100).toFixed(0)}%` +
          `\n       Time: ${us}µs  (target <${TARGET_US}µs)` +
          (!fast ? " ⚠ SLOW" : "") +
          `\n       Confidence: ${(result.confidence * 100).toFixed(0)}%\n`,
      );
    } catch (err) {
      failed++;
      console.log(`[FAIL] solve() — ${tc.name}: ${err.message}\n`);
    }
  }
}

// ── Benchmark ───────────────────────────────────────────────────────

console.log(`─────────────────────────────────────────────`);
console.log(`Benchmarking equity() × ${ITERATIONS} iterations...\n`);

if (typeof addon.equity === "function") {
  const heroB = [48, 44, 40, 36, 32];
  const boardB = [50, 44, 38];

  // Warmup
  for (let i = 0; i < 10; i++) addon.equity(heroB, boardB, SIMS);

  const times = [];
  for (let i = 0; i < ITERATIONS; i++) {
    const t0 = performance.now();
    addon.equity(heroB, boardB, SIMS);
    times.push((performance.now() - t0) * 1000); // µs
  }

  times.sort((a, b) => a - b);
  const avg = times.reduce((a, b) => a + b, 0) / times.length;
  const p50 = times[Math.floor(times.length * 0.5)];
  const p95 = times[Math.floor(times.length * 0.95)];
  const p99 = times[Math.floor(times.length * 0.99)];
  const min = times[0];
  const max = times[times.length - 1];

  console.log(`  PLO5 equity(5000 sims) benchmark:`);
  console.log(`    avg: ${avg.toFixed(0)}µs`);
  console.log(`    p50: ${p50.toFixed(0)}µs`);
  console.log(`    p95: ${p95.toFixed(0)}µs`);
  console.log(`    p99: ${p99.toFixed(0)}µs`);
  console.log(`    min: ${min.toFixed(0)}µs  max: ${max.toFixed(0)}µs`);
  console.log(
    `    ${avg < TARGET_US ? "✓ WITHIN" : "✗ EXCEEDS"} target (<${TARGET_US}µs)`,
  );
}

// ── Summary ─────────────────────────────────────────────────────────

console.log(`\n─────────────────────────────────────────────`);
console.log(`Results: ${passed} passed, ${failed} failed`);
console.log(
  failed === 0
    ? "\n✓ All tests passed — Rust N-API bridge is operational.\n"
    : "\n✗ Some tests failed. Check logs above.\n",
);
process.exit(failed > 0 ? 1 : 0);

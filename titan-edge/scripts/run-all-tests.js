/**
 * Titan Edge — Master Test Runner
 *
 * Runs all test suites sequentially and reports aggregated results.
 *
 * Usage:
 *   node scripts/run-all-tests.js           # Run all suites
 *   node scripts/run-all-tests.js --native   # Include native addon smoke tests
 *
 * Test Suites:
 *   1. GTO Engine (brain/gto-engine)       — Decision engine unit tests
 *   2. Game Loop (game-loop)               — State machine unit tests
 *   3. Solver Bridge (brain/solver-bridge)  — Bridge unit + integration tests
 *   4. Native Smoke (scripts/test-native)   — Rust N-API addon (--native flag)
 */

"use strict";

const { execSync } = require("node:child_process");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");

const SUITES = [
  {
    name: "GTO Engine",
    script: path.join(ROOT, "src/main/brain/__tests__/gto-engine.test.js"),
    required: true,
  },
  {
    name: "Game Loop",
    script: path.join(ROOT, "src/main/__tests__/game-loop.test.js"),
    required: true,
  },
  {
    name: "Solver Bridge",
    script: path.join(ROOT, "src/main/brain/__tests__/solver-bridge.test.js"),
    required: true,
  },
];

// Optional native smoke test
if (process.argv.includes("--native")) {
  SUITES.push({
    name: "Native Addon Smoke",
    script: path.join(ROOT, "scripts/test-native-bridge.js"),
    required: false,
  });
}

console.log("╔══════════════════════════════════════════════════════════╗");
console.log("║    TITAN EDGE — FULL TEST SUITE                        ║");
console.log("╠══════════════════════════════════════════════════════════╣");
console.log(`║  Suites: ${SUITES.length}`.padEnd(59) + "║");
console.log(`║  Node: ${process.version}`.padEnd(59) + "║");
console.log(
  `║  Platform: ${process.platform}-${process.arch}`.padEnd(59) + "║",
);
console.log("╚══════════════════════════════════════════════════════════╝");

const results = [];
let allPassed = true;

for (const suite of SUITES) {
  console.log(`\n${"═".repeat(60)}`);
  console.log(`  Running: ${suite.name}`);
  console.log(`${"═".repeat(60)}`);

  const startMs = Date.now();
  try {
    const output = execSync(`node "${suite.script}"`, {
      cwd: ROOT,
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
      timeout: 30_000,
    });
    const elapsedMs = Date.now() - startMs;
    console.log(output);
    results.push({ name: suite.name, status: "PASS", elapsedMs });
  } catch (err) {
    const elapsedMs = Date.now() - startMs;
    // Print stdout even on failure
    if (err.stdout) console.log(err.stdout);
    if (err.stderr) console.error(err.stderr);

    if (suite.required) {
      allPassed = false;
      results.push({ name: suite.name, status: "FAIL", elapsedMs });
    } else {
      results.push({ name: suite.name, status: "SKIP", elapsedMs });
    }
  }
}

// ── Aggregate Summary ───────────────────────────────────────────────

console.log("\n" + "═".repeat(60));
console.log("  AGGREGATE RESULTS");
console.log("═".repeat(60));

for (const r of results) {
  const icon = r.status === "PASS" ? "✓" : r.status === "FAIL" ? "✗" : "○";
  console.log(
    `  ${icon} ${r.name.padEnd(30)} ${r.status.padEnd(6)} ${r.elapsedMs}ms`,
  );
}

const passed = results.filter((r) => r.status === "PASS").length;
const failed = results.filter((r) => r.status === "FAIL").length;
const skipped = results.filter((r) => r.status === "SKIP").length;

console.log(
  `\n  Total: ${passed} passed, ${failed} failed, ${skipped} skipped`,
);
console.log(
  allPassed ? "\n  ✓ ALL SUITES PASSED\n" : "\n  ✗ SOME SUITES FAILED\n",
);

process.exit(allPassed ? 0 : 1);

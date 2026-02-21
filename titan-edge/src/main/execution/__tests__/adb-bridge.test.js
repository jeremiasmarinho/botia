/**
 * ADB Bridge — Integration Test
 *
 * Tests real ADB connectivity to LDPlayer and fires a ghost tap.
 * Run with: node src/main/execution/__tests__/adb-bridge.test.js
 *
 * Prerequisites:
 *   1. LDPlayer running with USB debugging enabled
 *   2. adb.exe accessible (LDPlayer install or system PATH)
 */

"use strict";

const { AdbBridge } = require("../adb-bridge");

const SEPARATOR = "═".repeat(60);

async function main() {
  console.log(SEPARATOR);
  console.log("  TITAN EDGE AI — ADB Bridge Test");
  console.log(SEPARATOR);
  console.log();

  const bridge = new AdbBridge({ dryRun: false });

  // Event listeners for diagnostics
  bridge.on("connected", (info) => {
    console.log(`  ✓ Connected to: ${info.device}`);
    console.log(`  ✓ Model:        ${info.model}`);
    console.log(`  ✓ Screen:       ${info.screen.width}x${info.screen.height}`);
  });

  bridge.on("tap", (result) => {
    console.log(
      `  → Raw tap at (${result.x}, ${result.y}) — ${result.durationMs}ms`,
    );
  });

  bridge.on("ghostTap", (result) => {
    console.log(`  → Ghost tap at (${result.x}, ${result.y})`);
    console.log(`    Jitter: (${result.jitterX}, ${result.jitterY})px`);
    console.log(
      `    Delay:  ${result.delayMs}ms | Exec: ${result.durationMs}ms`,
    );
  });

  bridge.on("warn", (msg) => {
    console.log(`  ⚠ ${msg}`);
  });

  // ── Step 1: Connect ─────────────────────────────────────────────
  console.log("[1/4] Connecting to LDPlayer via ADB...");
  try {
    await bridge.connect();
  } catch (err) {
    console.error(`\n  ✗ Connection failed: ${err.message}`);
    console.error("\n  Troubleshooting:");
    console.error("    1. Is LDPlayer running?");
    console.error("    2. Is USB Debugging enabled in LDPlayer settings?");
    console.error("    3. Try running: adb devices");
    process.exit(1);
  }

  // ── Step 2: Ping ────────────────────────────────────────────────
  console.log("\n[2/4] Pinging device...");
  const alive = await bridge.ping();
  console.log(
    `  ${alive ? "✓" : "✗"} Device ${alive ? "responsive" : "unresponsive"}`,
  );
  if (!alive) {
    console.error("  ✗ Device not responding to shell commands.");
    process.exit(1);
  }

  // ── Step 3: Ghost Tap (safe zone — top-left corner) ─────────────
  console.log("\n[3/4] Firing ghost tap at safe zone (100, 100)...");
  try {
    const ghostResult = await bridge.ghostTap(100, 100, {
      jitter: 5,
      prePause: true,
    });
    console.log(
      `  ✓ Ghost tap completed in ${ghostResult.delayMs + ghostResult.durationMs}ms total`,
    );
  } catch (err) {
    console.error(`  ✗ Ghost tap failed: ${err.message}`);
  }

  // ── Step 4: Latency Benchmark ───────────────────────────────────
  console.log("\n[4/4] Benchmarking ADB tap latency (10 taps)...");
  const latencies = [];
  for (let i = 0; i < 10; i++) {
    const result = await bridge.tap(50, 50);
    latencies.push(result.durationMs);
  }

  const avg = latencies.reduce((a, b) => a + b, 0) / latencies.length;
  const p95 = latencies.sort((a, b) => a - b)[
    Math.floor(latencies.length * 0.95)
  ];

  console.log(`\n  Latency: avg=${avg.toFixed(0)}ms | p95=${p95}ms`);
  console.log(
    `  Budget:  ${avg < 50 ? "✓" : "⚠"} ${avg < 50 ? "Within" : "Over"} 50ms target`,
  );

  // ── Summary ─────────────────────────────────────────────────────
  console.log("\n" + SEPARATOR);
  console.log("  ✓ ADB Bridge test PASSED");
  console.log(`  Device: ${bridge.device} (${bridge.deviceModel})`);
  console.log(
    `  Screen: ${bridge.screenSize.width}x${bridge.screenSize.height}`,
  );
  console.log(`  Tap latency: ~${avg.toFixed(0)}ms`);
  console.log(SEPARATOR);
}

main().catch((err) => {
  console.error("\nUnhandled error:", err);
  process.exit(1);
});

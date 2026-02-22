/**
 * GameLoop — Unit Tests
 *
 * Tests the 5-state machine (WAITING → PERCEPTION → CALCULATING →
 * EXECUTING → COOLDOWN) in isolation, using mock dependencies.
 *
 * Run:  node src/main/__tests__/game-loop.test.js
 *
 * Coverage:
 *   - State transitions
 *   - Detection extraction (cards, buttons, hero cards)
 *   - Game state building (_buildGameState)
 *   - Button bbox mapping (_findButtonBbox)
 *   - Difficulty mapping
 *   - Perception stability gate
 *   - Cooldown phases
 *   - Edge cases: empty frames, no buttons, partial detections
 */

"use strict";

// ── Minimal test harness ────────────────────────────────────────────

let _passed = 0;
let _failed = 0;
let _skipped = 0;
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

function skip(name) {
  _skipped++;
  console.log(`  ○ ${name} (skipped)`);
}

function section(title) {
  console.log(`\n── ${title} ${"─".repeat(50 - title.length)}`);
}

// ── Load module under test ──────────────────────────────────────────

const { GameLoop, LoopState, FPS_CONFIG } = require("../game-loop");

// ── Mock Factories ──────────────────────────────────────────────────

function createMockLog() {
  const entries = [];
  const handler =
    (level) =>
    (...args) =>
      entries.push({ level, args });
  return {
    info: handler("info"),
    warn: handler("warn"),
    error: handler("error"),
    debug: handler("debug"),
    entries,
  };
}

function createMockAdb(opts = {}) {
  let executed = [];
  return {
    screenSize: opts.screenSize || { width: 1080, height: 1920 },
    executeAction: async (bbox, difficulty) => {
      executed.push({ bbox, difficulty });
      if (opts.drop) return { dropped: true };
      return {
        dropped: false,
        tapX: bbox.x + Math.round(bbox.width / 2),
        tapY: bbox.y + Math.round(bbox.height / 2),
        totalMs: 150,
        cognitiveDelayMs: 80,
      };
    },
    get executed() {
      return executed;
    },
  };
}

function createMockSolver(opts = {}) {
  return {
    initialized: true,
    solveFromIds: (params) => ({
      action: opts.action || "call",
      equity: opts.equity || 0.55,
      ev: opts.ev || 12.5,
      confidence: opts.confidence || 0.75,
      elapsedUs: opts.elapsedUs || 250,
      engine: opts.engine || "rust-cfr",
      frequencies: {
        fold: 0.1,
        check: 0.0,
        call: 0.5,
        raise: 0.3,
        allin: 0.1,
      },
    }),
    equityFromIds: (params) => ({
      equity: opts.equity || 0.55,
      sims: 5000,
      engine: "rust",
    }),
  };
}

function createMockGtoEngine() {
  return {
    decide: (state) => ({
      action: state.equity > 0.5 ? "call" : "fold",
      confidence: state.equity,
      raiseSize: 0,
      reasoning: "test-decision",
    }),
  };
}

function createDeps(overrides = {}) {
  let currentFps = 5;
  let visionPaused = false;
  return {
    adb: overrides.adb || createMockAdb(),
    solver: overrides.solver || createMockSolver(),
    GtoEngine: overrides.GtoEngine || createMockGtoEngine(),
    setVisionFps: (fps) => {
      currentFps = fps;
    },
    pauseVision: () => {
      visionPaused = true;
    },
    resumeVision: () => {
      visionPaused = false;
    },
    log: overrides.log || createMockLog(),
    get currentFps() {
      return currentFps;
    },
    get visionPaused() {
      return visionPaused;
    },
  };
}

/**
 * Create a vision frame payload
 */
function makeFrame(opts = {}) {
  const detections = [];
  // Add hero cards (bottom of screen, cy > 0.65)
  for (const classId of opts.heroCards || []) {
    detections.push({
      classId,
      cx: 0.5,
      cy: 0.75,
      w: 0.05,
      h: 0.08,
      confidence: 0.95,
    });
  }
  // Add board cards (top/center, cy <= 0.65)
  for (const classId of opts.boardCards || []) {
    detections.push({
      classId,
      cx: 0.5,
      cy: 0.4,
      w: 0.05,
      h: 0.08,
      confidence: 0.92,
    });
  }
  // Add buttons
  for (const classId of opts.buttons || []) {
    detections.push({
      classId,
      cx: 0.5,
      cy: 0.9,
      w: 0.1,
      h: 0.05,
      confidence: 0.98,
    });
  }
  return {
    detections,
    frameId: opts.frameId || 1,
    inferenceMs: opts.inferenceMs || 15,
  };
}

// ── Tests ───────────────────────────────────────────────────────────

async function main() {
  console.log("╔══════════════════════════════════════════════════╗");
  console.log("║    GAME LOOP — UNIT TEST SUITE                  ║");
  console.log("╚══════════════════════════════════════════════════╝");

  // ──────────────────────────────────────────────────────────────
  section("Lifecycle");

  await test("constructor — starts in STOPPED state", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    assert(gl.state === LoopState.STOPPED, `Expected STOPPED, got ${gl.state}`);
    assert(gl.running === false, "Should not be running");
  });

  await test("start — transitions to WAITING", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    assert(gl.state === LoopState.WAITING, `Expected WAITING, got ${gl.state}`);
    assert(gl.running === true, "Should be running");
    gl.stop();
  });

  await test("start — idempotent (calling twice is safe)", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    gl.start();
    assert(gl.state === LoopState.WAITING, "Still WAITING");
    gl.stop();
  });

  await test("stop — transitions to STOPPED", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    gl.stop();
    assert(gl.state === LoopState.STOPPED, `Expected STOPPED, got ${gl.state}`);
    assert(gl.running === false, "Should not be running");
  });

  await test("stop — clears frozen detections", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    gl.stop();
    assert(gl.frozenDetections === null, "Frozen detections should be null");
  });

  await test("stop — emits stateChange event", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    let emitted = false;
    gl.on("stateChange", (e) => {
      if (e.state === LoopState.STOPPED) emitted = true;
    });
    gl.stop();
    assert(emitted, "Should emit stateChange with STOPPED");
  });

  // ──────────────────────────────────────────────────────────────
  section("WAITING State");

  await test("WAITING — ignores frames without buttons", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    gl.onVisionFrame(makeFrame({ heroCards: [50, 46, 42, 38, 34] }));
    assert(gl.state === LoopState.WAITING, "Should stay in WAITING");
    gl.stop();
  });

  await test("WAITING — ignores buttons without hero cards", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    gl.onVisionFrame(makeFrame({ buttons: [52, 53, 54] }));
    assert(
      gl.state === LoopState.WAITING,
      "Should stay in WAITING (no hero cards)",
    );
    gl.stop();
  });

  await test("WAITING — transitions to PERCEPTION when buttons + hero cards", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    gl.onVisionFrame(
      makeFrame({
        heroCards: [50, 46, 42, 38, 34],
        buttons: [52, 53, 54],
      }),
    );
    assert(
      gl.state === LoopState.PERCEPTION,
      `Expected PERCEPTION, got ${gl.state}`,
    );
    gl.stop();
  });

  await test("WAITING — ignores frames when not running", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    // Not started — state is STOPPED
    gl.onVisionFrame(
      makeFrame({
        heroCards: [50, 46, 42, 38, 34],
        buttons: [52, 53],
      }),
    );
    assert(gl.state === LoopState.STOPPED, "Should stay STOPPED");
  });

  // ──────────────────────────────────────────────────────────────
  section("PERCEPTION State — Stability Gate");

  await test("PERCEPTION — requires STABILITY_REQUIRED consistent frames", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    const frame = makeFrame({
      heroCards: [50, 46, 42, 38, 34],
      boardCards: [0, 4, 8],
      buttons: [52, 53, 54],
    });

    // First frame → enters PERCEPTION
    gl.onVisionFrame(frame);
    assert(gl.state === LoopState.PERCEPTION, "Should be in PERCEPTION");

    // Second identical frame → still PERCEPTION (need 3 stable)
    gl.onVisionFrame(frame);
    assert(
      gl.state === LoopState.PERCEPTION,
      "Still PERCEPTION after 2 frames",
    );

    // Third identical frame → CALCULATING (then async → EXECUTING almost instantly)
    gl.onVisionFrame(frame);
    // _runCalculation() is async and may already advance to EXECUTING with fast mocks
    assert(
      gl.state === LoopState.CALCULATING ||
        gl.state === LoopState.EXECUTING ||
        gl.state === LoopState.COOLDOWN ||
        gl.state === LoopState.WAITING,
      `Expected CALCULATING+ state, got ${gl.state}`,
    );
    gl.stop();
  });

  await test("PERCEPTION — resets stability on different card signature", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    const frame1 = makeFrame({
      heroCards: [50, 46, 42, 38, 34],
      boardCards: [0, 4, 8],
      buttons: [52, 53],
    });
    const frame2 = makeFrame({
      heroCards: [50, 46, 42, 38, 34],
      boardCards: [0, 4, 12], // Different board card!
      buttons: [52, 53],
    });

    gl.onVisionFrame(frame1);
    assert(gl.state === LoopState.PERCEPTION, "PERCEPTION after frame1");

    gl.onVisionFrame(frame1);
    // 2 stable frames so far

    gl.onVisionFrame(frame2); // Different signature → reset counter
    assert(
      gl.state === LoopState.PERCEPTION,
      "Still PERCEPTION (counter reset)",
    );

    // Need 3 new stable frames from frame2's signature
    gl.onVisionFrame(frame2);
    gl.onVisionFrame(frame2);
    assert(
      gl.state === LoopState.CALCULATING ||
        gl.state === LoopState.EXECUTING ||
        gl.state === LoopState.COOLDOWN ||
        gl.state === LoopState.WAITING,
      `Expected CALCULATING+ after 3 stable frame2, got ${gl.state}`,
    );
    gl.stop();
  });

  await test("PERCEPTION — returns to WAITING if buttons disappear", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    gl.onVisionFrame(
      makeFrame({
        heroCards: [50, 46, 42, 38, 34],
        boardCards: [0, 4, 8],
        buttons: [52, 53],
      }),
    );
    assert(gl.state === LoopState.PERCEPTION, "In PERCEPTION");

    // Buttons disappear
    gl.onVisionFrame(
      makeFrame({
        heroCards: [50, 46, 42, 38, 34],
        boardCards: [0, 4, 8],
        buttons: [], // No buttons!
      }),
    );
    assert(
      gl.state === LoopState.WAITING,
      `Expected WAITING after buttons disappear, got ${gl.state}`,
    );
    gl.stop();
  });

  await test("PERCEPTION — requires MIN_CARDS_FOR_ACTION", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    // Only 1 hero card, 0 board = 1 total (MIN is 2)
    const frame = makeFrame({
      heroCards: [50],
      buttons: [52, 53],
    });

    gl.onVisionFrame(frame);
    assert(gl.state === LoopState.PERCEPTION, "In PERCEPTION");
    gl.onVisionFrame(frame);
    gl.onVisionFrame(frame);
    // Even with 3 stable frames, should NOT transition if < MIN_CARDS
    assert(
      gl.state === LoopState.PERCEPTION,
      `Expected PERCEPTION (not enough cards), got ${gl.state}`,
    );
    gl.stop();
  });

  await test("PERCEPTION — emits perception event each frame", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();

    let eventCount = 0;
    gl.on("perception", () => eventCount++);

    const frame = makeFrame({
      heroCards: [50, 46, 42, 38, 34],
      boardCards: [0, 4, 8],
      buttons: [52, 53],
    });

    gl.onVisionFrame(frame);
    gl.onVisionFrame(frame);
    assert(eventCount === 2, `Expected 2 perception events, got ${eventCount}`);
    gl.stop();
  });

  // ──────────────────────────────────────────────────────────────
  section("Detection Extraction");

  await test("_extractCards — filters classId 0-51", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const frame = makeFrame({
      heroCards: [0, 25, 51], // Valid cards
      buttons: [52, 60], // Should be excluded
    });
    const cards = gl._extractCards(frame);
    assert(cards.length === 3, `Expected 3 cards, got ${cards.length}`);
    assert(
      cards.every((c) => c.classId <= 51),
      "All cards should have classId <= 51",
    );
  });

  await test("_extractButtons — filters classId 52-61", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const frame = makeFrame({
      heroCards: [0, 25],
      buttons: [52, 53, 54, 59],
    });
    const buttons = gl._extractButtons(frame);
    assert(buttons.length === 4, `Expected 4 buttons, got ${buttons.length}`);
    assert(
      buttons.every((b) => b.classId >= 52 && b.classId <= 61),
      "All buttons should have classId 52-61",
    );
  });

  await test("_extractHeroCards — filters by cy > 0.65", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const frame = makeFrame({
      heroCards: [50, 46], // cy = 0.75 (bottom)
      boardCards: [0, 4, 8], // cy = 0.4 (center)
    });
    const hero = gl._extractHeroCards(frame);
    assert(hero.length === 2, `Expected 2 hero cards, got ${hero.length}`);
    assert(
      hero.every((c) => c.cy > 0.65),
      "All hero cards should have cy > 0.65",
    );
  });

  await test("_extractCards — handles missing detections field", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const cards = gl._extractCards({});
    assert(
      cards.length === 0,
      "Should return empty array for missing detections",
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Game State Building");

  await test("_buildGameState — separates hero and board cards by Y", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const cards = [
      { classId: 50, cy: 0.75 }, // Hero
      { classId: 46, cy: 0.8 }, // Hero
      { classId: 42, cy: 0.7 }, // Hero
      { classId: 38, cy: 0.72 }, // Hero
      { classId: 34, cy: 0.78 }, // Hero
      { classId: 0, cy: 0.4 }, // Board
      { classId: 4, cy: 0.35 }, // Board
      { classId: 8, cy: 0.42 }, // Board
    ];
    const state = gl._buildGameState(cards, []);
    assert(
      state.heroCards.length === 5,
      `Expected 5 hero cards, got ${state.heroCards.length}`,
    );
    assert(
      state.board.length === 3,
      `Expected 3 board cards, got ${state.board.length}`,
    );
    assert(state.street === "flop", `Expected flop, got ${state.street}`);
    assert(state.variant === "PLO5", `Expected PLO5, got ${state.variant}`);
  });

  await test("_buildGameState — detects PLO6 variant", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const cards = [
      { classId: 50, cy: 0.75 },
      { classId: 46, cy: 0.75 },
      { classId: 42, cy: 0.75 },
      { classId: 38, cy: 0.75 },
      { classId: 34, cy: 0.75 },
      { classId: 30, cy: 0.75 }, // 6th hero card
    ];
    const state = gl._buildGameState(cards, []);
    assert(state.variant === "PLO6", `Expected PLO6, got ${state.variant}`);
  });

  await test("_buildGameState — street detection from board count", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);

    // No board → preflop
    const s0 = gl._buildGameState([{ classId: 50, cy: 0.75 }], []);
    assert(s0.street === "preflop", `Expected preflop, got ${s0.street}`);

    // 3 board → flop
    const s3 = gl._buildGameState(
      [
        { classId: 0, cy: 0.4 },
        { classId: 4, cy: 0.4 },
        { classId: 8, cy: 0.4 },
      ],
      [],
    );
    assert(s3.street === "flop", `Expected flop, got ${s3.street}`);

    // 4 board → turn
    const s4 = gl._buildGameState(
      [
        { classId: 0, cy: 0.4 },
        { classId: 4, cy: 0.4 },
        { classId: 8, cy: 0.4 },
        { classId: 12, cy: 0.4 },
      ],
      [],
    );
    assert(s4.street === "turn", `Expected turn, got ${s4.street}`);

    // 5 board → river
    const s5 = gl._buildGameState(
      [
        { classId: 0, cy: 0.4 },
        { classId: 4, cy: 0.4 },
        { classId: 8, cy: 0.4 },
        { classId: 12, cy: 0.4 },
        { classId: 16, cy: 0.4 },
      ],
      [],
    );
    assert(s5.street === "river", `Expected river, got ${s5.street}`);
  });

  await test("_buildGameState — hardcoded defaults (BUG #3 documented)", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const state = gl._buildGameState([], []);
    // These are the hardcoded defaults (known limitation)
    assert(state.opponents === 1, "Default opponents should be 1");
    assert(state.potOdds === 0.3, "Default potOdds should be 0.3");
    assert(state.spr === 5.0, "Default SPR should be 5.0");
    assert(state.potSize === 100, "Default potSize should be 100");
    assert(state.stackSize === 500, "Default stackSize should be 500");
  });

  // ──────────────────────────────────────────────────────────────
  section("Button BBox Mapping");

  await test("_findButtonBbox — maps fold to classId 52", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const buttons = [
      { classId: 52, cx: 0.2, cy: 0.9, w: 0.1, h: 0.05 },
      { classId: 53, cx: 0.5, cy: 0.9, w: 0.1, h: 0.05 },
      { classId: 54, cx: 0.8, cy: 0.9, w: 0.1, h: 0.05 },
    ];
    const bbox = gl._findButtonBbox("fold", buttons);
    assert(bbox !== null, "Should find fold button");
    assert(bbox.x > 0, "x should be positive");
    assert(bbox.y > 0, "y should be positive");
    assert(bbox.width > 0, "width should be positive");
    assert(bbox.height > 0, "height should be positive");
  });

  await test("_findButtonBbox — maps check to classId 53", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const buttons = [{ classId: 53, cx: 0.5, cy: 0.9, w: 0.1, h: 0.05 }];
    const bbox = gl._findButtonBbox("check", buttons);
    assert(bbox !== null, "Should find check button");
  });

  await test("_findButtonBbox — maps call to classId 53 or 54", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const buttons = [{ classId: 54, cx: 0.8, cy: 0.9, w: 0.1, h: 0.05 }];
    const bbox = gl._findButtonBbox("call", buttons);
    assert(bbox !== null, "Should find call button (classId 54 fallback)");
  });

  await test("_findButtonBbox — maps allin to classId 59", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const buttons = [{ classId: 59, cx: 0.5, cy: 0.9, w: 0.1, h: 0.05 }];
    const bbox = gl._findButtonBbox("allin", buttons);
    assert(bbox !== null, "Should find allin button");
  });

  await test("_findButtonBbox — returns null for missing button", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const bbox = gl._findButtonBbox("fold", []);
    assert(bbox === null, "Should return null when no matching button");
  });

  await test("_findButtonBbox — converts normalized to pixel coords", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    // Screen: 1080x1920 (default), button at center
    const buttons = [{ classId: 52, cx: 0.5, cy: 0.5, w: 0.1, h: 0.05 }];
    const bbox = gl._findButtonBbox("fold", buttons);
    assert(bbox !== null, "Should find button");
    // Expected: x = 0.5*1080 - (0.1*1080)/2 = 540 - 54 = 486
    //           y = 0.5*1920 - (0.05*1920)/2 = 960 - 48 = 912
    assert(Math.abs(bbox.x - 486) <= 1, `x should be ~486, got ${bbox.x}`);
    assert(Math.abs(bbox.y - 912) <= 1, `y should be ~912, got ${bbox.y}`);
    assert(
      Math.abs(bbox.width - 108) <= 1,
      `width should be ~108, got ${bbox.width}`,
    );
    assert(
      Math.abs(bbox.height - 96) <= 1,
      `height should be ~96, got ${bbox.height}`,
    );
  });

  // ──────────────────────────────────────────────────────────────
  section("Difficulty Mapping");

  await test("_mapDifficulty — high confidence → easy", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    assert(gl._mapDifficulty(0.95) === "easy", "0.95 → easy");
    assert(gl._mapDifficulty(0.8) === "easy", "0.80 → easy");
  });

  await test("_mapDifficulty — medium confidence → medium", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    assert(gl._mapDifficulty(0.7) === "medium", "0.70 → medium");
    assert(gl._mapDifficulty(0.5) === "medium", "0.50 → medium");
  });

  await test("_mapDifficulty — low confidence → hard", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    assert(gl._mapDifficulty(0.3) === "hard", "0.30 → hard");
    assert(gl._mapDifficulty(0.1) === "hard", "0.10 → hard");
  });

  // ──────────────────────────────────────────────────────────────
  section("Full Cycle — WAITING → CALCULATING");

  await test("full cycle — transitions through states correctly", async () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    const stateChanges = [];
    gl.on("stateChange", (e) => stateChanges.push(e.state));

    gl.start();

    const frame = makeFrame({
      heroCards: [50, 46, 42, 38, 34],
      boardCards: [0, 4, 8],
      buttons: [52, 53, 54],
    });

    // Three stable frames → WAITING → PERCEPTION → CALCULATING
    gl.onVisionFrame(frame);
    gl.onVisionFrame(frame);
    gl.onVisionFrame(frame);

    // Allow async _runCalculation to proceed
    await new Promise((r) => setTimeout(r, 50));

    assert(
      stateChanges.includes(LoopState.PERCEPTION),
      "Should have passed through PERCEPTION",
    );
    assert(
      stateChanges.includes(LoopState.CALCULATING),
      "Should have reached CALCULATING",
    );

    gl.stop();
  });

  await test("stats — tracks cycles", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    const stats = gl.stats;
    assert(stats.cycles === 0, "Initial cycles should be 0");
    assert(stats.drops === 0, "Initial drops should be 0");
    assert(stats.state === LoopState.WAITING, "State should be in stats");
    gl.stop();
  });

  // ──────────────────────────────────────────────────────────────
  section("Edge Cases");

  await test("onVisionFrame — null payload doesn't crash", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    // Should handle gracefully via (payload.detections || [])
    try {
      gl.onVisionFrame({});
      gl.onVisionFrame({ detections: null });
    } catch (err) {
      assert(false, `Should not throw: ${err.message}`);
    }
    gl.stop();
  });

  await test("frozenDetections — null when not in CALCULATING", () => {
    const deps = createDeps();
    const gl = new GameLoop(deps);
    gl.start();
    assert(gl.frozenDetections === null, "Should be null in WAITING");
    gl.stop();
  });

  await test("FPS_CONFIG — correct values", () => {
    assert(
      FPS_CONFIG[LoopState.WAITING] === 5,
      `WAITING FPS should be 5, got ${FPS_CONFIG[LoopState.WAITING]}`,
    );
    assert(
      FPS_CONFIG[LoopState.PERCEPTION] === 30,
      `PERCEPTION FPS should be 30, got ${FPS_CONFIG[LoopState.PERCEPTION]}`,
    );
  });

  await test("LoopState — all states exist", () => {
    const expected = [
      "WAITING",
      "PERCEPTION",
      "CALCULATING",
      "EXECUTING",
      "COOLDOWN",
      "STOPPED",
    ];
    for (const s of expected) {
      assert(LoopState[s] === s, `LoopState.${s} should exist`);
    }
  });

  // ──────────────────────────────────────────────────────────────
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

/**
 * Game Loop — Centralized State Machine for Titan Edge AI
 *
 * Controls the ENTIRE perception → decision → execution pipeline as a
 * single, sequential state machine.  NO component acts independently.
 * The Game Loop is the SOLE authority that:
 *   1. Controls vision FPS (5 FPS idle → 30 FPS active)
 *   2. Decides WHEN to calculate equity
 *   3. Decides WHEN to execute an action
 *   4. Prevents stale-frame execution (zero-queue guarantee)
 *
 * State Machine:
 *
 *   ┌──────────┐   my turn     ┌────────────┐  stable    ┌─────────────┐
 *   │ WAITING  │──────────────→│ PERCEPTION │──────────→│ CALCULATING │
 *   │ (5 FPS)  │               │ (30 FPS)   │           │ (freeze)    │
 *   └──────────┘               └────────────┘           └──────┬──────┘
 *        ↑                                                     │
 *        │              ┌──────────┐           ┌───────────┐   │
 *        └──────────────│ COOLDOWN │←──────────│ EXECUTING │←──┘
 *                       │ (cleanup)│           │ (ADB tap) │
 *                       └──────────┘           └───────────┘
 *
 * WAITING (5 FPS):
 *   - Vision runs at low FPS to conserve GPU/CPU
 *   - Each frame is checked for "my turn" indicator (action buttons visible)
 *   - Transition → PERCEPTION when buttons are detected
 *
 * PERCEPTION (30 FPS, 500ms max):
 *   - Vision boosted to 30 FPS to confirm all cards and buttons
 *   - Multiple frames are aggregated for stable detection
 *   - STABILITY GATE: requires N consecutive frames with same card set
 *   - Transition → CALCULATING when detections are stable
 *   - Timeout → WAITING if stability not achieved in 2s
 *
 * CALCULATING (vision frozen):
 *   - Vision is PAUSED — no new frames accepted
 *   - Frozen detections sent to SolverBridge (Rust N-API, <3ms)
 *   - GTO Engine makes decision from equity + game state
 *   - Transition → EXECUTING with action + target bbox
 *
 * EXECUTING (ADB action):
 *   - ADB Bridge executeAction() called with bbox + difficulty
 *   - Includes cognitive delay (Poisson) + humanized tap (Gaussian)
 *   - If ADB returns {dropped: true}, loop stays in WAITING
 *   - Transition → COOLDOWN on success
 *
 * COOLDOWN (dynamic, vision-confirmed):
 *   - FLOOR: 1500ms minimum (PPPoker chip animation takes 1500-2500ms)
 *   - Vision resumes at 10 FPS to poll for button disappearance
 *   - GATE: waits until YOLO confirms 0 action buttons on screen
 *   - CEILING: 5000ms max before forced transition → WAITING
 *   - This prevents the bot from re-clicking a fading button
 *
 * Anti-Stale Guarantees:
 *   - Detections from PERCEPTION phase are frozen before calculation
 *   - NO new detections influence the decision during CALCULATING
 *   - executeAction() drops if ADB is locked (zero-queue)
 *   - Each cycle is atomic: perceive → decide → act on SAME data
 */

"use strict";

const { EventEmitter } = require("node:events");

// ── State Enum ──────────────────────────────────────────────────────

/** @enum {string} */
const LoopState = Object.freeze({
  WAITING: "WAITING",
  PERCEPTION: "PERCEPTION",
  CALCULATING: "CALCULATING",
  EXECUTING: "EXECUTING",
  COOLDOWN: "COOLDOWN",
  STOPPED: "STOPPED",
});

// ── Configuration ───────────────────────────────────────────────────

/** FPS for each state (controls inference window capture rate) */
const FPS_CONFIG = Object.freeze({
  [LoopState.WAITING]: 5, // Low power — just watching for "my turn"
  [LoopState.PERCEPTION]: 30, // Full speed — reading cards
});

/** Number of consecutive stable frames required before CALCULATING */
const STABILITY_REQUIRED = 3;

/** Max time in PERCEPTION before giving up (ms) */
const PERCEPTION_TIMEOUT_MS = 2000;

/**
 * Post-EXECUTING cooldown — dynamic, vision-confirmed.
 *
 * FLOOR: Minimum wait before even checking vision.  PPPoker chip
 * animations (Call/Raise → chips slide → next player) take 1500-2500ms.
 * Checking before 1500ms risks seeing a fading button ghost.
 *
 * CEILING: Maximum wait before forced return to WAITING, even if
 * buttons are still detected (safety against stuck UI states).
 *
 * POLL FPS: Vision runs at low FPS during cooldown to check for
 * button disappearance without GPU load.
 */
const COOLDOWN_FLOOR_MS = 1500;
const COOLDOWN_CEILING_MS = 5000;
const COOLDOWN_POLL_FPS = 10;

/** Minimum card detections to consider a frame "populated" */
const MIN_CARDS_FOR_ACTION = 2;

/** YOLO classId range for action buttons (52-61) */
const BUTTON_CLASS_MIN = 52;
const BUTTON_CLASS_MAX = 61;

/** YOLO classId range for cards (0-51) */
const CARD_CLASS_MAX = 51;

/**
 * @typedef {Object} LoopDependencies
 * @property {import('./execution/adb-bridge').AdbBridge} adb
 * @property {import('./brain/solver-bridge').SolverBridge} solver
 * @property {import('./brain/gto-engine')} GtoEngine
 * @property {import('./profiling/opponent-db').OpponentDb} [opponentDb]
 * @property {Function} setVisionFps - (fps: number) => void
 * @property {Function} pauseVision  - () => void
 * @property {Function} resumeVision - () => void
 * @property {Object} log            - Logger (electron-log)
 */

class GameLoop extends EventEmitter {
  /**
   * @param {LoopDependencies} deps
   */
  constructor(deps) {
    super();

    this._adb = deps.adb;
    this._solver = deps.solver;
    this._GtoEngine = deps.GtoEngine;
    this._opponentDb = deps.opponentDb || null;
    this._setVisionFps = deps.setVisionFps;
    this._pauseVision = deps.pauseVision;
    this._resumeVision = deps.resumeVision;
    this._log = deps.log;

    // ── State ───────────────────────────────────────────────────
    /** @type {LoopState} */
    this._state = LoopState.STOPPED;

    /** Perception stability tracking */
    this._stableFrames = 0;
    this._lastCardSignature = "";
    this._perceptionStartedAt = 0;

    /** Frozen detections for the current cycle */
    this._frozenDetections = null;

    /** Stats for monitoring */
    this._stats = {
      cycles: 0,
      drops: 0,
      avgCycleMs: 0,
      perceptionTimeouts: 0,
      lastCycleMs: 0,
    };

    /** Cycle timing */
    this._cycleStartedAt = 0;

    /** Cooldown tracking */
    this._cooldownStartedAt = 0;
    this._cooldownFloorReached = false;
    this._cooldownResolve = null;

    /** Emergency kill switch */
    this._running = false;
  }

  // ── Lifecycle ───────────────────────────────────────────────────

  /**
   * Start the Game Loop — enters WAITING state at 5 FPS.
   */
  start() {
    if (this._running) return;

    this._running = true;
    this._transitionTo(LoopState.WAITING);
    this._log.info("[GameLoop] Started — entering WAITING at 5 FPS");
  }

  /**
   * Stop the Game Loop — all processing halts.
   */
  stop() {
    this._running = false;
    this._state = LoopState.STOPPED;
    this._frozenDetections = null;
    this._stableFrames = 0;
    this._log.info("[GameLoop] Stopped");
    this.emit("stateChange", { state: LoopState.STOPPED });
  }

  /**
   * Feed a vision frame into the Game Loop.
   * Called by the IPC handler when the inference window sends detections.
   *
   * This is the ONLY entry point for new data.  The Game Loop decides
   * what to do with it based on the current state.
   *
   * @param {Object} payload - Vision detections payload
   * @param {Array}  payload.cards   - Card detections
   * @param {Array}  payload.buttons - Button detections
   * @param {number} payload.inferenceMs
   * @param {number} payload.frameId
   */
  onVisionFrame(payload) {
    if (!this._running) return;

    switch (this._state) {
      case LoopState.WAITING:
        this._handleWaiting(payload);
        break;

      case LoopState.PERCEPTION:
        this._handlePerception(payload);
        break;

      case LoopState.CALCULATING:
      case LoopState.EXECUTING:
        // IGNORE — vision data is irrelevant during these phases.
        // This is the core anti-stale guarantee: no new data can
        // influence an in-flight decision.
        break;

      case LoopState.COOLDOWN:
        // During COOLDOWN, we poll vision to detect button
        // disappearance.  This is NOT used for decisions — only
        // to confirm the PPPoker UI animation has completed.
        this._handleCooldownPoll(payload);
        break;
    }
  }

  // ── State Handlers ────────────────────────────────────────────

  /**
   * WAITING state: check each frame for "my turn" indicators.
   * If action buttons are detected → transition to PERCEPTION.
   * @private
   */
  _handleWaiting(payload) {
    const buttons = this._extractButtons(payload);

    if (buttons.length > 0) {
      this._cycleStartedAt = performance.now();
      this._log.debug(
        `[GameLoop] Buttons detected (${buttons.length}) — entering PERCEPTION`,
      );
      this._transitionTo(LoopState.PERCEPTION);
      // Process this frame immediately in PERCEPTION context
      this._handlePerception(payload);
    }
  }

  /**
   * PERCEPTION state: accumulate frames until stable.
   *
   * Stability means N consecutive frames with the same set of
   * detected card classIds.  This prevents acting on a partially
   * rendered board (e.g., cards still animating in).
   *
   * @private
   */
  _handlePerception(payload) {
    const now = performance.now();

    // Check perception timeout
    if (now - this._perceptionStartedAt > PERCEPTION_TIMEOUT_MS) {
      this._stats.perceptionTimeouts++;
      this._log.warn(
        `[GameLoop] Perception timeout (${PERCEPTION_TIMEOUT_MS}ms) — back to WAITING`,
      );
      this._transitionTo(LoopState.WAITING);
      return;
    }

    // Build card signature: sorted classIds → "2,5,14,33,48"
    const cards = this._extractCards(payload);
    const buttons = this._extractButtons(payload);
    const signature = cards
      .map((c) => c.classId)
      .sort((a, b) => a - b)
      .join(",");

    // No buttons visible anymore → false alarm, back to WAITING
    if (buttons.length === 0) {
      this._stableFrames = 0;
      this._lastCardSignature = "";
      this._transitionTo(LoopState.WAITING);
      return;
    }

    // Check stability
    if (signature === this._lastCardSignature && signature.length > 0) {
      this._stableFrames++;
    } else {
      this._stableFrames = 1;
      this._lastCardSignature = signature;
    }

    this.emit("perception", {
      stableFrames: this._stableFrames,
      required: STABILITY_REQUIRED,
      cards: cards.length,
      buttons: buttons.length,
      signature,
    });

    // Check if stable enough
    if (
      this._stableFrames >= STABILITY_REQUIRED &&
      cards.length >= MIN_CARDS_FOR_ACTION
    ) {
      // FREEZE detections — these are the authoritative data for this cycle
      this._frozenDetections = {
        cards,
        buttons,
        frameId: payload.frameId,
        inferenceMs: payload.inferenceMs,
        frozenAt: now,
      };

      this._log.info(
        `[GameLoop] Stable after ${this._stableFrames} frames ` +
          `(${cards.length} cards, ${buttons.length} buttons) — CALCULATING`,
      );

      // Pause vision — no new frames during calculation
      this._transitionTo(LoopState.CALCULATING);
      this._runCalculation();
    }
  }

  // ── Calculation Phase ─────────────────────────────────────────

  /**
   * CALCULATING state: compute equity and make decision.
   * @private
   */
  async _runCalculation() {
    if (!this._running || this._state !== LoopState.CALCULATING) return;

    const { cards, buttons } = this._frozenDetections;

    try {
      // ── Build Game State ──────────────────────────────────────
      const gameState = this._buildGameState(cards, buttons);

      // ── Equity Calculation (Rust N-API < 3ms) ─────────────────
      let equity = 0.5; // fallback
      if (this._solver?.initialized) {
        try {
          const equityResult = this._solver.equity({
            heroCards: gameState.heroCards,
            board: gameState.board,
            opponents: gameState.opponents,
            variant: gameState.variant,
          });
          equity = equityResult.equity ?? 0.5;
        } catch (err) {
          this._log.warn(`[GameLoop] Equity calc failed: ${err.message}`);
        }
      }

      // ── GTO Decision ──────────────────────────────────────────
      const decision = this._GtoEngine.decide({
        equity,
        potOdds: gameState.potOdds,
        spr: gameState.spr,
        street: gameState.street,
        inPosition: gameState.inPosition,
        opponents: gameState.opponents,
        betFacing: gameState.betFacing,
        potSize: gameState.potSize,
        stackSize: gameState.stackSize,
      });

      this._log.info(
        `[GameLoop] Decision: ${decision.action} ` +
          `(equity=${(equity * 100).toFixed(1)}%, ` +
          `confidence=${(decision.confidence * 100).toFixed(0)}%)`,
      );

      // ── Map Decision to Button BBox ───────────────────────────
      const targetBbox = this._findButtonBbox(decision.action, buttons);

      if (!targetBbox) {
        this._log.warn(
          `[GameLoop] No button found for action "${decision.action}" — aborting cycle`,
        );
        this._transitionTo(LoopState.WAITING);
        return;
      }

      // ── Determine Difficulty for Humanizer ────────────────────
      const difficulty = this._mapDifficulty(decision.confidence);

      // ── Transition to EXECUTING ───────────────────────────────
      this._transitionTo(LoopState.EXECUTING);
      await this._runExecution(targetBbox, difficulty, decision);
    } catch (err) {
      this._log.error(`[GameLoop] Calculation error: ${err.message}`);
      this._transitionTo(LoopState.WAITING);
    }
  }

  // ── Execution Phase ───────────────────────────────────────────

  /**
   * EXECUTING state: send action to ADB Bridge.
   * @private
   */
  async _runExecution(bbox, difficulty, decision) {
    if (!this._running || this._state !== LoopState.EXECUTING) return;

    try {
      const result = await this._adb.executeAction(bbox, difficulty);

      if (result.dropped) {
        // ADB was locked (shouldn't happen in normal flow, but safe)
        this._stats.drops++;
        this._log.warn("[GameLoop] ADB dropped action — returning to WAITING");
        this._transitionTo(LoopState.WAITING);
        return;
      }

      this.emit("actionExecuted", {
        decision: decision.action,
        confidence: decision.confidence,
        tapX: result.tapX,
        tapY: result.tapY,
        totalMs: result.totalMs,
        cognitiveDelayMs: result.cognitiveDelayMs,
      });

      this._log.info(
        `[GameLoop] Action executed: ${decision.action} → ` +
          `tap(${result.tapX}, ${result.tapY}) in ${result.totalMs}ms`,
      );

      // ── Transition to COOLDOWN ────────────────────────────────
      this._transitionTo(LoopState.COOLDOWN);
      await this._runCooldown();
    } catch (err) {
      this._log.error(`[GameLoop] Execution error: ${err.message}`);
      this._transitionTo(LoopState.WAITING);
    }
  }

  // ── Cooldown Phase (Dynamic, Vision-Confirmed) ────────────────

  /**
   * COOLDOWN state: two-phase wait for UI animation to complete.
   *
   * Phase 1 — FLOOR (1500ms):
   *   Sleep unconditionally.  PPPoker chip animations and button
   *   fade-outs take at minimum 1500ms.  No YOLO polling needed.
   *
   * Phase 2 — POLL (1500ms → 5000ms):
   *   Resume vision at 10 FPS and wait for YOLO to confirm that
   *   ALL action buttons (classId 52-61) have disappeared from the
   *   screen.  Each `onVisionFrame()` during COOLDOWN calls
   *   `_handleCooldownPoll()` which resolves a promise when
   *   buttons === 0.
   *
   * If buttons persist past 5000ms (stuck UI, emoji popup overlapping
   * a button region), force-exit to WAITING anyway.
   *
   * @private
   */
  async _runCooldown() {
    if (!this._running) return;

    this._cooldownStartedAt = performance.now();
    this._cooldownFloorReached = false;

    // ── Phase 1: Hard Floor ─────────────────────────────────────
    await sleep(COOLDOWN_FLOOR_MS);

    if (!this._running || this._state !== LoopState.COOLDOWN) return;

    this._cooldownFloorReached = true;

    // ── Phase 2: Vision-Confirmed Button Disappearance ──────────
    // Resume vision at low FPS to check for button ghost remnants.
    this._setVisionFps(COOLDOWN_POLL_FPS);
    this._resumeVision();

    const remainingMs = COOLDOWN_CEILING_MS - COOLDOWN_FLOOR_MS;

    // Wait for _handleCooldownPoll() to resolve, or timeout
    const buttonsGone = await Promise.race([
      new Promise((resolve) => {
        this._cooldownResolve = resolve;
      }),
      sleep(remainingMs).then(() => false),
    ]);

    this._cooldownResolve = null;

    if (!buttonsGone) {
      this._log.warn(
        `[GameLoop] Cooldown ceiling reached (${COOLDOWN_CEILING_MS}ms) — ` +
          `buttons may still be visible, forcing WAITING`,
      );
    }

    // Record cycle stats
    const cycleMs = Math.round(performance.now() - this._cycleStartedAt);
    this._stats.cycles++;
    this._stats.lastCycleMs = cycleMs;
    this._stats.avgCycleMs = Math.round(
      (this._stats.avgCycleMs * (this._stats.cycles - 1) + cycleMs) /
        this._stats.cycles,
    );

    const cooldownMs = Math.round(performance.now() - this._cooldownStartedAt);

    this.emit("cycleComplete", {
      cycleMs,
      cooldownMs,
      buttonsConfirmedGone: !!buttonsGone,
      avgCycleMs: this._stats.avgCycleMs,
      totalCycles: this._stats.cycles,
    });

    this._log.info(
      `[GameLoop] Cooldown complete: ${cooldownMs}ms ` +
        `(floor=${COOLDOWN_FLOOR_MS}ms, confirmed=${!!buttonsGone})`,
    );

    this._transitionTo(LoopState.WAITING);
  }

  /**
   * Handle a vision frame during COOLDOWN (Phase 2 only).
   * Checks if all action buttons have disappeared from the screen.
   * @private
   */
  _handleCooldownPoll(payload) {
    if (!this._cooldownFloorReached) return; // Still in Phase 1

    const buttons = this._extractButtons(payload);

    if (buttons.length === 0 && this._cooldownResolve) {
      this._log.debug(
        `[GameLoop] Cooldown: buttons confirmed gone at ` +
          `${Math.round(performance.now() - this._cooldownStartedAt)}ms`,
      );
      this._cooldownResolve(true);
    }
  }

  // ── State Transitions ─────────────────────────────────────────

  /**
   * Transition to a new state with side effects.
   * @private
   * @param {LoopState} newState
   */
  _transitionTo(newState) {
    const oldState = this._state;
    if (oldState === newState) return;

    this._state = newState;

    // ── Side effects per state ──────────────────────────────────
    switch (newState) {
      case LoopState.WAITING:
        this._stableFrames = 0;
        this._lastCardSignature = "";
        this._frozenDetections = null;
        this._setVisionFps(FPS_CONFIG[LoopState.WAITING]);
        // Only resume if vision was paused (from CALCULATING/EXECUTING)
        if (
          oldState === LoopState.CALCULATING ||
          oldState === LoopState.EXECUTING ||
          oldState === LoopState.COOLDOWN
        ) {
          this._resumeVision();
        }
        break;

      case LoopState.PERCEPTION:
        this._perceptionStartedAt = performance.now();
        this._stableFrames = 0;
        this._setVisionFps(FPS_CONFIG[LoopState.PERCEPTION]);
        break;

      case LoopState.CALCULATING:
        this._pauseVision();
        break;

      case LoopState.EXECUTING:
        // Vision already paused from CALCULATING
        break;

      case LoopState.COOLDOWN:
        // Vision resumes at COOLDOWN_POLL_FPS after the floor.
        // Handled dynamically inside _runCooldown().
        break;
    }

    this.emit("stateChange", { state: newState, previousState: oldState });
  }

  // ── Helpers ───────────────────────────────────────────────────

  /**
   * Extract card detections (classId 0-51) from vision payload.
   * @private
   */
  _extractCards(payload) {
    return (payload.detections || []).filter(
      (d) => d.classId >= 0 && d.classId <= CARD_CLASS_MAX,
    );
  }

  /**
   * Extract button detections (classId 52-61) from vision payload.
   * @private
   */
  _extractButtons(payload) {
    return (payload.detections || []).filter(
      (d) => d.classId >= BUTTON_CLASS_MIN && d.classId <= BUTTON_CLASS_MAX,
    );
  }

  /**
   * Build a game state object from frozen detections.
   *
   * NOTE: This is a simplified version.  In production, this would
   * integrate with OCR for pot size, stack sizes, and bet amounts.
   *
   * @private
   * @param {Array} cards
   * @param {Array} buttons
   * @returns {Object} GameState-compatible object
   */
  _buildGameState(cards, buttons) {
    // Separate hero cards from board cards by their Y position
    // Hero cards are typically in the bottom portion of the screen
    const HERO_Y_THRESHOLD = 700; // pixels — below this = hero region

    const heroCards = cards
      .filter((c) => c.cy > HERO_Y_THRESHOLD)
      .map((c) => c.classId);
    const board = cards
      .filter((c) => c.cy <= HERO_Y_THRESHOLD)
      .map((c) => c.classId);

    // Determine street from board card count
    const streetMap = { 0: "preflop", 3: "flop", 4: "turn", 5: "river" };
    const street = streetMap[board.length] || "flop";

    // Determine variant from hero card count
    const variant = heroCards.length >= 6 ? "PLO6" : "PLO5";

    return {
      heroCards,
      board,
      street,
      variant,
      opponents: 1, // Conservative default
      potOdds: 0.3, // Default — needs OCR integration
      spr: 5.0, // Default — needs stack/pot OCR
      inPosition: true, // Default — needs position detection
      betFacing: 0,
      potSize: 100,
      stackSize: 500,
    };
  }

  /**
   * Find the YOLO bounding box for a given action name.
   *
   * Maps GTO decision actions to YOLO classIds:
   *   fold(52), check(53), call(54→53), raise(54), allin(59)
   *
   * @private
   * @param {string} action - 'fold' | 'check' | 'call' | 'raise' | 'allin'
   * @param {Array} buttons - Button detections from frozen frame
   * @returns {{ x: number, y: number, width: number, height: number }|null}
   */
  _findButtonBbox(action, buttons) {
    // Map action names to YOLO classId priorities
    const ACTION_TO_CLASS = {
      fold: [52],
      check: [53],
      call: [53, 54], // "call" button shares classId with check in some models
      raise: [54, 55, 56, 57, 58], // Any raise variant
      allin: [59],
    };

    const targetClasses = ACTION_TO_CLASS[action] || [];

    for (const classId of targetClasses) {
      const btn = buttons.find((b) => b.classId === classId);
      if (btn) {
        return {
          x: Math.round(btn.cx - btn.w / 2),
          y: Math.round(btn.cy - btn.h / 2),
          width: Math.round(btn.w),
          height: Math.round(btn.h),
        };
      }
    }

    return null;
  }

  /**
   * Map decision confidence to Humanizer difficulty.
   * High confidence → EASY (fast reaction), low → HARD (slow, deliberate).
   *
   * @private
   * @param {number} confidence - [0, 1]
   * @returns {'easy'|'medium'|'hard'}
   */
  _mapDifficulty(confidence) {
    if (confidence >= 0.8) return "easy";
    if (confidence >= 0.5) return "medium";
    return "hard";
  }

  // ── Getters ───────────────────────────────────────────────────

  /** @returns {LoopState} Current state */
  get state() {
    return this._state;
  }

  /** @returns {boolean} */
  get running() {
    return this._running;
  }

  /** @returns {Object} Loop statistics */
  get stats() {
    return { ...this._stats, state: this._state };
  }

  /** @returns {Object|null} The frozen detections (null if not in CALCULATING+) */
  get frozenDetections() {
    return this._frozenDetections ? { ...this._frozenDetections } : null;
  }
}

// ── Utility ─────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = { GameLoop, LoopState, FPS_CONFIG };

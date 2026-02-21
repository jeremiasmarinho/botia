/**
 * Equity Pool — Worker Thread Pool Manager
 *
 * Distributes Monte Carlo simulations across N Worker Threads,
 * aggregates results, and returns unified equity estimates.
 *
 * Architecture Decision: N = min(4, os.cpus / 2)
 *   - 4 workers on the i7-10700 (8C/16T) leaves 12 threads for:
 *     Electron main, renderer (WebGPU/YOLO), OS, and LDPlayer.
 *   - Each worker gets sims/N simulations to run in parallel.
 *   - Results are merged: total_wins / total_runs = equity.
 *
 * Memory: Each worker is ~15MB RSS. Pool = ~60MB total.
 *
 * Usage:
 *   const pool = new EquityPool({ size: 4 });
 *   await pool.init();
 *   const result = await pool.calculate({
 *     hero: ['Ah', 'Kh', 'Qh', 'Jh', 'Th'],  // PLO5 hand
 *     board: ['2c', '7d', '9s'],
 *     dead: [],
 *     sims: 5000,
 *     opponents: 2,
 *   });
 *   console.log(result.equity); // 0.42
 */

"use strict";

const { Worker } = require("node:worker_threads");
const os = require("node:os");
const path = require("node:path");
const { EventEmitter } = require("node:events");
const { MONTE_CARLO, TIMING } = require("../../shared/constants");

const WORKER_PATH = path.join(__dirname, "equity-worker.js");

class EquityPool extends EventEmitter {
  /**
   * @param {Object} [opts]
   * @param {number} [opts.size]       - Number of workers (default: auto)
   * @param {number} [opts.timeoutMs]  - Per-request timeout
   */
  constructor(opts = {}) {
    super();
    this._size =
      opts.size ||
      Math.min(MONTE_CARLO.POOL_SIZE, Math.floor(os.cpus().length / 2));
    this._timeout = opts.timeoutMs || TIMING.EQUITY_TIMEOUT_MS;
    this._workers = [];
    this._idCounter = 0;
    this._pending = new Map(); // id → { resolve, reject, timer }
    this._initialized = false;
  }

  /** Spawn worker threads and wait for ready signals. */
  async init() {
    if (this._initialized) return;

    const readyPromises = [];

    for (let i = 0; i < this._size; i++) {
      const worker = new Worker(WORKER_PATH);

      const readyP = new Promise((resolve, reject) => {
        const onMsg = (msg) => {
          if (msg.type === "ready") {
            worker.off("message", onMsg);
            resolve();
          }
        };
        worker.on("message", onMsg);
        worker.on("error", reject);
      });

      worker.on("message", (msg) => this._onWorkerMessage(i, msg));
      worker.on("error", (err) => this._onWorkerError(i, err));

      this._workers.push({ worker, busy: false });
      readyPromises.push(readyP);
    }

    await Promise.all(readyPromises);
    this._initialized = true;
    this.emit("ready", { size: this._size });
  }

  /**
   * Calculate Omaha equity using the worker pool.
   *
   * Splits the simulation count across all workers, runs in parallel,
   * and merges results.
   *
   * @param {Object} params
   * @param {string[]} params.hero       - Hero's hole cards
   * @param {string[]} params.board      - Board cards
   * @param {string[]} [params.dead=[]]  - Dead cards
   * @param {number}   [params.sims]     - Total simulations
   * @param {number}   [params.opponents=1]
   * @returns {Promise<{ equity: number, winRate: number, tieRate: number, sims: number, elapsedMs: number }>}
   */
  async calculate(params) {
    if (!this._initialized) {
      throw new Error("[EquityPool] Not initialized. Call init() first.");
    }

    const {
      hero,
      board = [],
      dead = [],
      sims = this._defaultSims(hero.length),
      opponents = 1,
    } = params;

    const t0 = performance.now();
    const simsPerWorker = Math.ceil(sims / this._size);

    // Dispatch to all workers in parallel
    const promises = this._workers.map((_, idx) =>
      this._dispatch(idx, {
        hero,
        board,
        dead,
        sims: simsPerWorker,
        opponents,
        handSize: hero.length,
      }),
    );

    const results = await Promise.all(promises);

    // Merge results
    let totalWins = 0;
    let totalTies = 0;
    let totalRuns = 0;

    for (const r of results) {
      totalWins += r.wins;
      totalTies += r.ties;
      totalRuns += r.runs;
    }

    const elapsedMs = performance.now() - t0;

    const merged = {
      equity: totalRuns > 0 ? (totalWins + totalTies * 0.5) / totalRuns : 0,
      winRate: totalRuns > 0 ? totalWins / totalRuns : 0,
      tieRate: totalRuns > 0 ? totalTies / totalRuns : 0,
      sims: totalRuns,
      elapsedMs: Math.round(elapsedMs * 10) / 10,
    };

    this.emit("result", merged);
    return merged;
  }

  /** Gracefully terminate all workers. */
  async shutdown() {
    const terminatePromises = this._workers.map(({ worker }) =>
      worker.terminate(),
    );
    await Promise.all(terminatePromises);
    this._workers = [];
    this._initialized = false;
    this.emit("shutdown");
  }

  // ── Internals ─────────────────────────────────────────────────

  /**
   * Select default simulation count based on hand size.
   * PLO6 has more combos per eval → fewer sims to stay within budget.
   */
  _defaultSims(handSize) {
    if (handSize >= 6) return MONTE_CARLO.PLO6_SIMS;
    if (handSize >= 5) return MONTE_CARLO.PLO5_SIMS;
    return MONTE_CARLO.DEFAULT_SIMS;
  }

  /** Send work to a specific worker and await the response. */
  _dispatch(workerIdx, payload) {
    return new Promise((resolve, reject) => {
      const id = ++this._idCounter;
      const { worker } = this._workers[workerIdx];

      const timer = setTimeout(() => {
        this._pending.delete(id);
        reject(
          new Error(
            `[EquityPool] Worker ${workerIdx} timed out (${this._timeout}ms)`,
          ),
        );
      }, this._timeout);

      this._pending.set(id, { resolve, reject, timer });
      worker.postMessage({ id, ...payload });
    });
  }

  _onWorkerMessage(workerIdx, msg) {
    if (msg.type === "ready") return; // Already handled in init

    const pending = this._pending.get(msg.id);
    if (!pending) return;

    clearTimeout(pending.timer);
    this._pending.delete(msg.id);
    pending.resolve(msg);
  }

  _onWorkerError(workerIdx, err) {
    this.emit("error", { workerIdx, error: err.message });
  }

  // ── Getters ───────────────────────────────────────────────────

  get size() {
    return this._size;
  }
  get initialized() {
    return this._initialized;
  }
}

module.exports = { EquityPool };

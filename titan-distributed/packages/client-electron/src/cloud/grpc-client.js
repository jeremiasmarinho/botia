/**
 * Titan Client-Electron — gRPC Client
 *
 * Connects the Electron edge client to the cloud gateway via gRPC.
 * Provides transparent fallback to local solver when cloud is unreachable.
 *
 * Architecture:
 *   Electron Main Process → THIS MODULE → gRPC (TLS) → Cloud Gateway
 *                                ↕
 *                          Local Solver (fallback)
 *
 * Features:
 *   - Automatic reconnection with exponential backoff
 *   - Latency monitoring (rejects if >50ms RTT)
 *   - Transparent local fallback
 *   - Server-streaming support for progressive equity updates
 *   - Multi-table bidirectional streaming
 */

"use strict";

const grpc = require("@grpc/grpc-js");
const protoLoader = require("@grpc/proto-loader");
const path = require("node:path");
const { EventEmitter } = require("node:events");

// ── Configuration ───────────────────────────────────────────────────

const PROTO_PATH = path.resolve(
  __dirname,
  "../../../proto/titan/v1/table_state.proto",
);

const DEFAULT_OPTIONS = {
  host: "localhost",
  port: 50051,
  useTls: false,
  maxLatencyMs: 50, // Reject cloud if RTT > 50ms
  reconnectBaseMs: 1000, // Exponential backoff base
  reconnectMaxMs: 30000, // Max backoff
  pingIntervalMs: 5000, // Health check interval
};

// ── Proto Loading ───────────────────────────────────────────────────

const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
  keepCase: true,
  longs: Number,
  enums: String,
  defaults: true,
  oneofs: true,
});

const titanProto = grpc.loadPackageDefinition(packageDefinition).titan.v1;

// ── GrpcClient Class ────────────────────────────────────────────────

class GrpcClient extends EventEmitter {
  /**
   * @param {object} opts - Connection options
   * @param {string} opts.host - Cloud gateway hostname
   * @param {number} opts.port - Cloud gateway port
   * @param {boolean} opts.useTls - Enable TLS
   * @param {number} opts.maxLatencyMs - Max acceptable RTT
   * @param {object} localSolver - Local solver fallback (EquityPool instance)
   */
  constructor(opts = {}, localSolver = null) {
    super();
    this._opts = { ...DEFAULT_OPTIONS, ...opts };
    this._localSolver = localSolver;
    this._client = null;
    this._connected = false;
    this._latencyMs = Infinity;
    this._reconnectAttempt = 0;
    this._pingTimer = null;
    this._multiTableStream = null;
  }

  // ── Connection Management ───────────────────────────────────────

  /**
   * Connect to the cloud gateway.
   */
  async connect() {
    const address = `${this._opts.host}:${this._opts.port}`;
    const credentials = this._opts.useTls
      ? grpc.credentials.createSsl()
      : grpc.credentials.createInsecure();

    this._client = new titanProto.TitanSolver(address, credentials, {
      "grpc.keepalive_time_ms": 10_000,
      "grpc.keepalive_timeout_ms": 5_000,
      "grpc.max_receive_message_length": 1024 * 1024,
    });

    // Wait for connection
    return new Promise((resolve, reject) => {
      const deadline = new Date(Date.now() + 5000);
      this._client.waitForReady(deadline, async (err) => {
        if (err) {
          console.warn(`[grpc-client] Cloud unreachable: ${err.message}`);
          this._connected = false;
          this._scheduleReconnect();
          resolve(false); // Don't reject — fallback to local
        } else {
          this._connected = true;
          this._reconnectAttempt = 0;
          console.log(`[grpc-client] Connected to ${address}`);

          // Measure initial latency
          await this._measureLatency();
          this._startPingLoop();

          this.emit("connected", { address, latencyMs: this._latencyMs });
          resolve(true);
        }
      });
    });
  }

  /**
   * Disconnect from the cloud gateway.
   */
  disconnect() {
    if (this._pingTimer) clearInterval(this._pingTimer);
    if (this._multiTableStream) this._multiTableStream.end();
    if (this._client) this._client.close();
    this._connected = false;
    this.emit("disconnected");
  }

  // ── Solve RPCs ────────────────────────────────────────────────────

  /**
   * Solve a single game state.
   * Falls back to local solver if cloud is unavailable or too slow.
   *
   * @param {object} tableState - Proto-compatible TableState object
   * @returns {Promise<object>} SolverResponse
   */
  async solve(tableState) {
    if (this._shouldUseLocal()) {
      return this._solveLocal(tableState);
    }

    return new Promise((resolve, reject) => {
      const t0 = performance.now();

      this._client.Solve(
        tableState,
        { deadline: new Date(Date.now() + 200) },
        (err, response) => {
          const elapsed = performance.now() - t0;

          if (err) {
            console.warn(
              `[grpc-client] Cloud solve failed (${elapsed.toFixed(0)}ms): ${err.message}`,
            );
            // Fallback to local
            this._solveLocal(tableState).then(resolve).catch(reject);
            return;
          }

          this._latencyMs = elapsed;
          response._source = "cloud";
          response._latencyMs = elapsed;
          resolve(response);
        },
      );
    });
  }

  /**
   * Solve with progressive updates (server-streaming).
   * Emits 'progress' events with intermediate equity estimates.
   *
   * @param {object} tableState - TableState
   * @returns {Promise<object>} Final SolverResponse
   */
  async solveStream(tableState) {
    if (this._shouldUseLocal()) {
      return this._solveLocal(tableState);
    }

    return new Promise((resolve, reject) => {
      const stream = this._client.SolveStream(tableState);

      stream.on("data", (update) => {
        if (update.type === "PROGRESS") {
          this.emit("progress", {
            equity: update.equity,
            progress: update.progress,
          });
        } else if (update.type === "COMPLETE") {
          update.response._source = "cloud-stream";
          resolve(update.response);
        }
      });

      stream.on("error", (err) => {
        console.warn(`[grpc-client] SolveStream error: ${err.message}`);
        this._solveLocal(tableState).then(resolve).catch(reject);
      });

      stream.on("end", () => {
        // If no COMPLETE was received, resolve with local
      });
    });
  }

  /**
   * Start a multi-table bidirectional stream.
   * Returns a function to send table states; results arrive via 'multi-solve' events.
   *
   * @returns {{ send: Function, close: Function }}
   */
  startMultiTable() {
    if (this._shouldUseLocal()) {
      return {
        send: async (state) => {
          const result = await this._solveLocal(state);
          this.emit("multi-solve", result);
        },
        close: () => {},
      };
    }

    const stream = this._client.SolveMultiTable();
    this._multiTableStream = stream;

    stream.on("data", (response) => {
      response._source = "cloud-multi";
      this.emit("multi-solve", response);
    });

    stream.on("error", (err) => {
      console.warn(`[grpc-client] MultiTable error: ${err.message}`);
      this._multiTableStream = null;
    });

    return {
      send: (state) => stream.write(state),
      close: () => {
        stream.end();
        this._multiTableStream = null;
      },
    };
  }

  // ── Opponent Profiling ────────────────────────────────────────────

  /**
   * Get an opponent profile from cloud DB.
   */
  async getOpponentProfile(opponentId) {
    if (!this._connected || !this._client) return null;

    return new Promise((resolve) => {
      this._client.GetOpponentProfile(
        { opponent_id: opponentId },
        { deadline: new Date(Date.now() + 2000) },
        (err, profile) => {
          if (err) {
            resolve(null);
            return;
          }
          resolve(profile);
        },
      );
    });
  }

  /**
   * Record a hand result in cloud DB.
   */
  async recordHandResult(handResult) {
    if (!this._connected || !this._client) return;

    return new Promise((resolve) => {
      this._client.RecordHandResult(handResult, (err) => {
        if (err)
          console.warn(`[grpc-client] RecordHand failed: ${err.message}`);
        resolve();
      });
    });
  }

  // ── Internal Methods ──────────────────────────────────────────────

  _shouldUseLocal() {
    return !this._connected || this._latencyMs > this._opts.maxLatencyMs;
  }

  async _solveLocal(tableState) {
    if (!this._localSolver) {
      throw new Error("No local solver available and cloud is unreachable");
    }

    // Adapt tableState proto format to local solver format
    const result = await this._localSolver.computeEquity({
      hero: tableState.hero_cards || [],
      board: tableState.board_cards || [],
      dead: tableState.dead_cards || [],
      opponents: tableState.num_players ? tableState.num_players - 1 : 1,
      handSize: tableState.format === "FORMAT_PLO6" ? 6 : 5,
    });

    return {
      equity: result.equity,
      recommended_action: "ACTION_CHECK", // Local solver is equity-only
      _source: "local-fallback",
      _latencyMs: result.elapsed,
    };
  }

  async _measureLatency() {
    return new Promise((resolve) => {
      const t0 = Date.now();
      this._client.Ping(
        { client_timestamp_ms: t0 },
        { deadline: new Date(Date.now() + 2000) },
        (err, response) => {
          if (err) {
            this._latencyMs = Infinity;
          } else {
            this._latencyMs = Date.now() - t0;
          }
          resolve(this._latencyMs);
        },
      );
    });
  }

  _startPingLoop() {
    this._pingTimer = setInterval(async () => {
      const latency = await this._measureLatency();
      this.emit("latency", { ms: latency });

      if (latency > this._opts.maxLatencyMs) {
        console.warn(
          `[grpc-client] High latency: ${latency}ms > ${this._opts.maxLatencyMs}ms threshold`,
        );
        this.emit("high-latency", { ms: latency });
      }
    }, this._opts.pingIntervalMs);
  }

  _scheduleReconnect() {
    const delay = Math.min(
      this._opts.reconnectBaseMs * Math.pow(2, this._reconnectAttempt),
      this._opts.reconnectMaxMs,
    );
    this._reconnectAttempt++;

    console.log(
      `[grpc-client] Reconnecting in ${delay}ms (attempt ${this._reconnectAttempt})`,
    );

    setTimeout(() => {
      this.connect().catch(() => {});
    }, delay);
  }

  // ── Status ────────────────────────────────────────────────────────

  get isConnected() {
    return this._connected;
  }
  get latency() {
    return this._latencyMs;
  }
  get source() {
    return this._shouldUseLocal() ? "local" : "cloud";
  }
}

module.exports = { GrpcClient };

/**
 * Titan Cloud Gateway — gRPC Server
 *
 * Entry point for the cloud-side solver. Receives TableState protobuf
 * messages from Electron clients, routes them to the Rust core-engine
 * via N-API, applies exploitative adjustments, and returns SolverResponse.
 *
 * Architecture:
 *   Electron Client → gRPC (TLS) → THIS SERVER → N-API → Rust Engine
 *                                        ↕
 *                                   PostgreSQL (Profiling)
 *                                        ↕
 *                                   MCP → LLM (Exploit Advisor)
 *
 * Deployment: Docker container on AWS EC2 (A100/H100) or GCP.
 * Scaling: Horizontal via Kubernetes ReplicaSet + gRPC load balancing.
 */

"use strict";

const grpc = require("@grpc/grpc-js");
const protoLoader = require("@grpc/proto-loader");
const path = require("node:path");
const { SolverBridge } = require("./solver-bridge");
const { ExploitativeLayer } = require("./exploitative");
const { PgStore } = require("./profiling/pg-store");
const { createLogger } = require("./logger");

// ── Configuration ───────────────────────────────────────────────────

const PORT = process.env.TITAN_PORT || "50051";
const HOST = process.env.TITAN_HOST || "0.0.0.0";
const PROTO_PATH = path.resolve(
  __dirname,
  "../../proto/titan/v1/table_state.proto",
);

const log = createLogger("gateway");

// ── Proto Loading ───────────────────────────────────────────────────

const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
  keepCase: true,
  longs: Number,
  enums: String,
  defaults: true,
  oneofs: true,
});

const titanProto = grpc.loadPackageDefinition(packageDefinition).titan.v1;

// ── Subsystem Initialization ────────────────────────────────────────

const solver = new SolverBridge();
const exploit = new ExploitativeLayer();
const db = new PgStore();

// ── RPC Implementations ─────────────────────────────────────────────

/**
 * Solve — Unary RPC
 * Receives a single TableState, computes the optimal action, returns response.
 */
async function solve(call, callback) {
  const t0 = performance.now();
  const state = call.request;

  try {
    log.info(
      {
        hand: state.hand_number,
        format: state.format,
        street: state.street,
        heroCards: state.hero_cards?.length,
      },
      "Solve request received",
    );

    // 1. Query opponent profiles (non-blocking)
    const opponentProfiles = await db.getProfiles(state.opponent_ids || []);

    // 2. Compute GTO strategy via Rust engine
    const gtoResult = solver.solve({
      format: state.format,
      street: state.street,
      heroCards: state.hero_cards,
      boardCards: state.board_cards,
      deadCards: state.dead_cards,
      potBb100: state.pot_size_bb100,
      heroStack: state.hero_stack_bb100,
      villainStacks: state.villain_stacks_bb100,
      actions: state.actions,
      position: state.hero_position,
      numPlayers: state.num_players,
    });

    // 3. Apply exploitative adjustments based on opponent profiles
    const adjusted = exploit.adjust(gtoResult, opponentProfiles, {
      street: state.street,
      position: state.hero_position,
    });

    const solveTimeUs = Math.round((performance.now() - t0) * 1000);

    const response = {
      session_id: state.session_id,
      hand_number: state.hand_number,
      recommended_action: adjusted.action,
      raise_amount_bb100: adjusted.raiseAmountBb100 || 0,
      fold_frequency: adjusted.frequencies.fold,
      check_frequency: adjusted.frequencies.check,
      call_frequency: adjusted.frequencies.call,
      raise_frequency: adjusted.frequencies.raise,
      allin_frequency: adjusted.frequencies.allin,
      equity: gtoResult.equity,
      ev_bb100: gtoResult.evBb100,
      exploit: {
        applied: adjusted.exploitApplied,
        aggression_modifier: adjusted.aggressionModifier,
        reasoning: adjusted.reasoning,
        opponent_archetype: adjusted.archetype,
      },
      solve_time_us: solveTimeUs,
      server_timestamp_ms: Date.now(),
      engine_version: solver.version,
    };

    log.info(
      {
        hand: state.hand_number,
        action: adjusted.action,
        equity: gtoResult.equity.toFixed(3),
        solveUs: solveTimeUs,
      },
      "Solve complete",
    );

    callback(null, response);
  } catch (err) {
    log.error({ err, hand: state.hand_number }, "Solve failed");
    callback({
      code: grpc.status.INTERNAL,
      message: `Solver error: ${err.message}`,
    });
  }
}

/**
 * SolveStream — Server-streaming RPC
 * Sends intermediate equity estimates followed by final decision.
 */
async function solveStream(call) {
  const state = call.request;

  try {
    // Send progress updates
    for (let progress = 0.25; progress <= 0.75; progress += 0.25) {
      const partialEquity = solver.partialSolve(state, progress);
      call.write({
        type: "PROGRESS",
        equity: partialEquity,
        progress,
        response: null,
      });
    }

    // Final solve
    const gtoResult = solver.solve(state);
    const opponentProfiles = await db.getProfiles(state.opponent_ids || []);
    const adjusted = exploit.adjust(gtoResult, opponentProfiles, {
      street: state.street,
      position: state.hero_position,
    });

    call.write({
      type: "COMPLETE",
      equity: gtoResult.equity,
      progress: 1.0,
      response: {
        session_id: state.session_id,
        hand_number: state.hand_number,
        recommended_action: adjusted.action,
        equity: gtoResult.equity,
        solve_time_us: 0,
        server_timestamp_ms: Date.now(),
        engine_version: solver.version,
      },
    });

    call.end();
  } catch (err) {
    log.error({ err }, "SolveStream failed");
    call.destroy(err);
  }
}

/**
 * SolveMultiTable — Bidirectional streaming RPC
 * Handles multiple concurrent table states for multi-tabling.
 */
function solveMultiTable(call) {
  call.on("data", async (state) => {
    try {
      const gtoResult = solver.solve(state);
      const profiles = await db.getProfiles(state.opponent_ids || []);
      const adjusted = exploit.adjust(gtoResult, profiles, {
        street: state.street,
        position: state.hero_position,
      });

      call.write({
        session_id: state.session_id,
        hand_number: state.hand_number,
        recommended_action: adjusted.action,
        raise_amount_bb100: adjusted.raiseAmountBb100 || 0,
        equity: gtoResult.equity,
        ev_bb100: gtoResult.evBb100,
        server_timestamp_ms: Date.now(),
        engine_version: solver.version,
      });
    } catch (err) {
      log.error({ err, table: state.table_id }, "MultiTable solve error");
    }
  });

  call.on("end", () => call.end());
  call.on("error", (err) => log.error({ err }, "MultiTable stream error"));
}

/**
 * GetOpponentProfile — Unary RPC
 */
async function getOpponentProfile(call, callback) {
  try {
    const profile = await db.getProfile(call.request.opponent_id);
    if (!profile) {
      callback({
        code: grpc.status.NOT_FOUND,
        message: `Opponent ${call.request.opponent_id} not found`,
      });
      return;
    }
    callback(null, profile);
  } catch (err) {
    callback({ code: grpc.status.INTERNAL, message: err.message });
  }
}

/**
 * RecordHandResult — Unary RPC
 */
async function recordHandResult(call, callback) {
  try {
    await db.recordHand(call.request);
    callback(null, { success: true, message: "Recorded" });
  } catch (err) {
    callback({ code: grpc.status.INTERNAL, message: err.message });
  }
}

/**
 * Ping — Health check with latency measurement.
 */
function ping(call, callback) {
  callback(null, {
    client_timestamp_ms: call.request.client_timestamp_ms,
    server_timestamp_ms: Date.now(),
    active_sessions: 0, // TODO: track active sessions
  });
}

// ── Server Bootstrap ────────────────────────────────────────────────

async function main() {
  log.info("Initializing Titan Cloud Gateway...");

  // Initialize subsystems
  await solver.init();
  await db.init();

  const server = new grpc.Server({
    "grpc.max_receive_message_length": 1024 * 1024, // 1MB
    "grpc.max_send_message_length": 1024 * 1024,
    "grpc.keepalive_time_ms": 10_000,
    "grpc.keepalive_timeout_ms": 5_000,
    "grpc.keepalive_permit_without_calls": 1,
  });

  server.addService(titanProto.TitanSolver.service, {
    Solve: solve,
    SolveStream: solveStream,
    SolveMultiTable: solveMultiTable,
    GetOpponentProfile: getOpponentProfile,
    RecordHandResult: recordHandResult,
    Ping: ping,
  });

  server.bindAsync(
    `${HOST}:${PORT}`,
    grpc.ServerCredentials.createInsecure(), // TODO: TLS in production
    (err, port) => {
      if (err) {
        log.fatal({ err }, "Failed to bind gRPC server");
        process.exit(1);
      }

      log.info({ host: HOST, port }, "═══ Titan Cloud Gateway ONLINE ═══");
      log.info(`  Engine:  ${solver.version}`);
      log.info(`  Format:  PLO5 / PLO6`);
      log.info(`  Proto:   titan.v1.TitanSolver`);
    },
  );
}

// ── Graceful Shutdown ───────────────────────────────────────────────

process.on("SIGTERM", async () => {
  log.info("SIGTERM received. Shutting down...");
  await db.close();
  process.exit(0);
});

process.on("SIGINT", async () => {
  log.info("SIGINT received. Shutting down...");
  await db.close();
  process.exit(0);
});

main().catch((err) => {
  log.fatal({ err }, "Gateway startup failed");
  process.exit(1);
});

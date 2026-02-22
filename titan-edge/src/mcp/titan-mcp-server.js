#!/usr/bin/env node
/**
 * Titan MCP Server — Model Context Protocol for offline debugging.
 *
 * Exposes the OpponentDb (SQLite) and variant thresholds as MCP
 * tools that any LLM client (Copilot, Claude, etc.) can query
 * via stdio transport.
 *
 * Usage:
 *   node src/mcp/titan-mcp-server.js [--db path/to/opponents.db]
 *
 * MCP Config (~/.vscode/mcp.json or workspace .vscode/mcp.json):
 *   {
 *     "servers": {
 *       "titan": {
 *         "type": "stdio",
 *         "command": "node",
 *         "args": ["src/mcp/titan-mcp-server.js"],
 *         "cwd": "${workspaceFolder}/titan-edge"
 *       }
 *     }
 *   }
 *
 * Tools exposed:
 *   getProfile       — Full opponent profile (stats, archetype, trust)
 *   listOpponents    — All profiled opponents for a variant
 *   findSimilar      — K-Nearest Neighbors by range-normalized distance
 *   getHistory       — Recent hand history for a player
 *   getThresholds    — Variant classification thresholds
 */

"use strict";

const {
  OpponentDb,
  VARIANT_THRESHOLDS,
  MIN_TRUST_HANDS,
} = require("../main/profiling/opponent-db");

// ── Parse CLI args ──────────────────────────────────────────────────

const args = process.argv.slice(2);
let dbPath = null;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--db" && args[i + 1]) dbPath = args[i + 1];
}

// ── Initialize DB ───────────────────────────────────────────────────

const db = new OpponentDb(dbPath);
db.init();

// ── MCP Protocol (JSON-RPC 2.0 over stdio) ─────────────────────────

const TOOLS = [
  {
    name: "getProfile",
    description:
      "Get the full computed profile for an opponent in a specific " +
      "poker variant (PLO5 or PLO6). Returns stats (VPIP, PFR, AF, " +
      "3-Bet, C-Bet, WTSD, etc.), archetype classification, trust " +
      "status, and raw counters. Returns null if the player doesn't exist.",
    inputSchema: {
      type: "object",
      properties: {
        playerId: {
          type: "string",
          description: "Unique player identifier (PPPoker club ID or OCR hash)",
        },
        variant: {
          type: "string",
          enum: ["PLO5", "PLO6"],
          description: "Game variant — stats are isolated per variant",
        },
      },
      required: ["playerId", "variant"],
    },
  },
  {
    name: "listOpponents",
    description:
      "List all profiled opponents for a variant, sorted by hands " +
      "played (descending). Each entry includes computed stats and " +
      "archetype. Use to get an overview of the player pool.",
    inputSchema: {
      type: "object",
      properties: {
        variant: {
          type: "string",
          enum: ["PLO5", "PLO6"],
          description: "Game variant",
        },
        limit: {
          type: "number",
          description: "Max players to return (default: 50)",
        },
      },
      required: ["variant"],
    },
  },
  {
    name: "findSimilar",
    description:
      "Find the K most similar opponents to a target player using " +
      "Range-Normalized Euclidean Distance. Compares VPIP, PFR, AF, " +
      "3-Bet, C-Bet, Fold-to-CBet, and WTSD — each normalized by " +
      "its natural variant range so no single stat dominates. " +
      "Only returns players that pass the trust gate (≥50 hands). " +
      "Use to find exploitative strategies that worked against " +
      "similar player profiles.",
    inputSchema: {
      type: "object",
      properties: {
        playerId: {
          type: "string",
          description: "Target player to find neighbors for",
        },
        variant: {
          type: "string",
          enum: ["PLO5", "PLO6"],
          description: "Game variant",
        },
        k: {
          type: "number",
          description: "Number of neighbors (default: 5)",
        },
      },
      required: ["playerId", "variant"],
    },
  },
  {
    name: "getHistory",
    description:
      "Get raw hand history (action log) for a player in a variant. " +
      "Returns recent actions with street, action type, amounts, and " +
      "pot sizes. Use to analyze specific behavioral patterns like " +
      "river bluff frequency or preflop limp tendencies.",
    inputSchema: {
      type: "object",
      properties: {
        playerId: {
          type: "string",
          description: "Player identifier",
        },
        variant: {
          type: "string",
          enum: ["PLO5", "PLO6"],
          description: "Game variant",
        },
        limit: {
          type: "number",
          description: "Max actions to return (default: 100)",
        },
      },
      required: ["playerId", "variant"],
    },
  },
  {
    name: "getThresholds",
    description:
      "Get the classification thresholds for a variant. Shows the " +
      "exact VPIP/PFR/AF/WTSD/CBet boundaries used to classify " +
      "opponents as nit/tag/lag/fish/whale/reg. Essential for " +
      "understanding why a player was classified a certain way. " +
      "Also returns MIN_TRUST_HANDS (the trust gate threshold).",
    inputSchema: {
      type: "object",
      properties: {
        variant: {
          type: "string",
          enum: ["PLO5", "PLO6"],
          description: "Game variant",
        },
      },
      required: ["variant"],
    },
  },
];

// ── Tool Execution ──────────────────────────────────────────────────

function executeTool(name, params) {
  switch (name) {
    case "getProfile": {
      const profile = db.getProfile(params.playerId, params.variant);
      return profile
        ? {
            content: [{ type: "text", text: JSON.stringify(profile, null, 2) }],
          }
        : {
            content: [
              {
                type: "text",
                text: `Player "${params.playerId}" not found for variant ${params.variant}.`,
              },
            ],
            isError: false,
          };
    }

    case "listOpponents": {
      const list = db.listAll(params.variant, params.limit || 50);
      const summary = list.map((p) => ({
        player_id: p.player_id,
        screen_name: p.screen_name,
        archetype: p.player_type,
        hands: p.hands_played,
        trusted: p.trusted,
        vpip: p.vpip,
        pfr: p.pfr,
        af: p.af,
      }));
      return {
        content: [{ type: "text", text: JSON.stringify(summary, null, 2) }],
      };
    }

    case "findSimilar": {
      const result = db.findSimilar(
        params.playerId,
        params.variant,
        params.k || 5,
      );
      if (!result.target) {
        return {
          content: [
            {
              type: "text",
              text: `Player "${params.playerId}" not found or below trust threshold.`,
            },
          ],
        };
      }
      const output = {
        target: {
          player_id: result.target.player_id,
          screen_name: result.target.screen_name,
          archetype: result.target.player_type,
          vpip: result.target.vpip,
          pfr: result.target.pfr,
          af: result.target.af,
          three_bet: result.target.three_bet,
        },
        neighbors: result.neighbors.map((n) => ({
          player_id: n.profile?.player_id,
          screen_name: n.screen_name,
          archetype: n.profile?.player_type,
          distance: n.distance,
          hands: n.hands_played,
          vpip: n.profile?.vpip,
          pfr: n.profile?.pfr,
          af: n.profile?.af,
          three_bet: n.profile?.three_bet,
        })),
      };
      return {
        content: [{ type: "text", text: JSON.stringify(output, null, 2) }],
      };
    }

    case "getHistory": {
      const history = db.getHistory(
        params.playerId,
        params.variant,
        params.limit || 100,
      );
      return {
        content: [{ type: "text", text: JSON.stringify(history, null, 2) }],
      };
    }

    case "getThresholds": {
      const thresholds = db.getThresholds(params.variant);
      const output = {
        variant: params.variant,
        min_trust_hands: MIN_TRUST_HANDS,
        thresholds,
      };
      return {
        content: [{ type: "text", text: JSON.stringify(output, null, 2) }],
      };
    }

    default:
      return {
        content: [{ type: "text", text: `Unknown tool: ${name}` }],
        isError: true,
      };
  }
}

// ── JSON-RPC 2.0 Dispatcher ────────────────────────────────────────

function handleRequest(msg) {
  const { id, method, params } = msg;

  switch (method) {
    case "initialize":
      return {
        jsonrpc: "2.0",
        id,
        result: {
          protocolVersion: "2024-11-05",
          serverInfo: { name: "titan-poker", version: "1.0.0" },
          capabilities: { tools: {} },
        },
      };

    case "notifications/initialized":
      // Client ack — no response needed
      return null;

    case "tools/list":
      return {
        jsonrpc: "2.0",
        id,
        result: { tools: TOOLS },
      };

    case "tools/call": {
      const { name, arguments: toolArgs } = params;
      try {
        const result = executeTool(name, toolArgs || {});
        return { jsonrpc: "2.0", id, result };
      } catch (err) {
        return {
          jsonrpc: "2.0",
          id,
          result: {
            content: [{ type: "text", text: `Error: ${err.message}` }],
            isError: true,
          },
        };
      }
    }

    case "ping":
      return { jsonrpc: "2.0", id, result: {} };

    default:
      return {
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: `Method not found: ${method}` },
      };
  }
}

// ── stdio Transport ────────────────────────────────────────────────

let buffer = "";

process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;

  // Process complete lines (newline-delimited JSON)
  let newlineIdx;
  while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
    const line = buffer.slice(0, newlineIdx).trim();
    buffer = buffer.slice(newlineIdx + 1);

    if (!line) continue;

    try {
      const msg = JSON.parse(line);
      const response = handleRequest(msg);
      if (response) {
        process.stdout.write(JSON.stringify(response) + "\n");
      }
    } catch (err) {
      const errResp = {
        jsonrpc: "2.0",
        id: null,
        error: { code: -32700, message: `Parse error: ${err.message}` },
      };
      process.stdout.write(JSON.stringify(errResp) + "\n");
    }
  }
});

process.on("SIGINT", () => {
  db.close();
  process.exit(0);
});

process.on("SIGTERM", () => {
  db.close();
  process.exit(0);
});

// Signal readiness (MCP convention — don't write anything before init)
process.stderr.write(
  "[titan-mcp] Server ready. Waiting for JSON-RPC on stdin.\n",
);

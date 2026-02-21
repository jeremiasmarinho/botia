/**
 * MCP Advisor — Model Context Protocol Bridge
 *
 * Exposes the opponent SQLite database to an LLM (Claude) via MCP,
 * enabling real-time tactical consultation:
 *
 *   "Player X has VPIP=72%, PFR=8%, AF=0.3 over 200 hands.
 *    They're a calling station. Value-bet thinner, don't bluff."
 *
 * Architecture:
 *   OpponentDb (SQLite) → MCP Server → Claude API → Tactical Advice
 *
 * This module provides the MCP resource/tool definitions.
 * The actual MCP server is started by the orchestrator.
 */

"use strict";

/**
 * Define MCP resources that expose opponent data to the LLM.
 *
 * @param {import('./opponent-db').OpponentDb} db
 * @returns {Object} MCP resource definitions
 */
function createMcpResources(db) {
  return {
    resources: [
      {
        uri: "titan://opponents/active",
        name: "Active Opponents",
        description:
          "Currently tracked opponent profiles with Omaha stats (VPIP, PFR, 3-Bet, AF)",
        mimeType: "application/json",
        handler: () => {
          const opponents = db.listAll(20);
          return JSON.stringify(opponents, null, 2);
        },
      },
      {
        uri: "titan://opponents/{playerId}/history",
        name: "Player Hand History",
        description: "Recent hand history for a specific opponent",
        mimeType: "application/json",
        handler: ({ playerId }) => {
          const history = db.getHistory(playerId, 50);
          return JSON.stringify(history, null, 2);
        },
      },
    ],

    tools: [
      {
        name: "get_opponent_profile",
        description:
          "Get full stats for an Omaha opponent (VPIP, PFR, 3-Bet%, AF, hands played)",
        inputSchema: {
          type: "object",
          properties: {
            playerId: { type: "string", description: "Player ID or alias" },
          },
          required: ["playerId"],
        },
        handler: ({ playerId }) => {
          const profile = db.get(playerId);
          if (!profile) return { error: `Player "${playerId}" not found` };
          return profile;
        },
      },
      {
        name: "classify_opponent",
        description:
          "Classify an opponent as LAG/TAG/LP/TP/Maniac based on Omaha stats",
        inputSchema: {
          type: "object",
          properties: {
            playerId: { type: "string" },
          },
          required: ["playerId"],
        },
        handler: ({ playerId }) => {
          const p = db.get(playerId);
          if (!p) return { error: "Not found" };

          let style = "Unknown";
          if (p.vpip > 0.5 && p.pfr > 0.2) style = "LAG (Loose-Aggressive)";
          else if (p.vpip <= 0.35 && p.pfr > 0.2)
            style = "TAG (Tight-Aggressive)";
          else if (p.vpip > 0.5 && p.pfr <= 0.1)
            style = "LP (Loose-Passive / Calling Station)";
          else if (p.vpip <= 0.35 && p.pfr <= 0.1)
            style = "TP (Tight-Passive / Nit)";
          else if (p.vpip > 0.7 && p.pfr > 0.35) style = "Maniac";

          return {
            playerId: p.player_id,
            style,
            vpip: p.vpip,
            pfr: p.pfr,
            af: p.af,
            hands: p.hands,
            recommendation: getRecommendation(style),
          };
        },
      },
    ],
  };
}

/**
 * Tactical recommendations per player type.
 * @param {string} style
 * @returns {string}
 */
function getRecommendation(style) {
  const recs = {
    "LAG (Loose-Aggressive)":
      "Trap with strong hands. Call down with nut draws. They bluff too much — let them hang themselves.",
    "TAG (Tight-Aggressive)":
      "Respect their raises. 3-bet only with premium PLO hands. Fold marginal spots — they have it.",
    "LP (Loose-Passive / Calling Station)":
      "Value-bet thinner (top pair+). Never bluff — they call with anything. Size up for value.",
    "TP (Tight-Passive / Nit)":
      "Steal relentlessly. Fold to their raises — when a nit raises, they have the nuts.",
    Maniac:
      "Tighten up, let them donate. 4-bet premium PLO hands. Flat call their 3-bets with suited rundowns.",
  };
  return recs[style] || "Insufficient data. Continue profiling.";
}

module.exports = { createMcpResources };

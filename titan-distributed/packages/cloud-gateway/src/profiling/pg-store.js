/**
 * Titan Cloud Gateway — PostgreSQL Opponent Profiling Store (v2)
 *
 * ═══════════════════════════════════════════════════════════════
 * CRITICAL DESIGN: Stats are VARIANT-ISOLATED (PLO5 vs PLO6).
 * ═══════════════════════════════════════════════════════════════
 *
 * Mirrors the SQLite schema from titan-edge in PostgreSQL for
 * cloud-scale, multi-session, multi-client persistent profiling.
 *
 * Tables:
 *   players       — identity (player_id, screen_name, type, notes)
 *   player_stats  — per-variant count-based stats (PLO5 row + PLO6 row)
 *   hand_history  — immutable action log with variant column
 *
 * Stats are stored as INTEGER COUNTS — never as percentages.
 * Percentages are computed at query time to prevent the
 * "1-hand maniac" illusion.  Trust gate: hands_played >= 50.
 *
 * Features:
 *   - Connection pooling via pg.Pool
 *   - Auto-migration v1→v2 (archives old opponents table)
 *   - Atomic increment-based stat updates (no full recalculation)
 *   - Variant-aware profile queries
 *   - Batch profile queries for multi-table support
 */

"use strict";

const { Pool } = require("pg");
const { createLogger } = require("../logger");

const log = createLogger("pg-store");

/**
 * Minimum hands before the engine trusts profiling data.
 * Below this threshold → pure GTO (no exploit adjustments).
 */
const MIN_TRUST_HANDS = 50;

// ── SQL Migrations ──────────────────────────────────────────────────

const MIGRATIONS = [
  // Migration 1: Archive the old flat-percentage opponents table
  `DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_name = 'opponents'
      AND   table_schema = current_schema()
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_name = '_legacy_opponents_v1'
      AND   table_schema = current_schema()
    ) THEN
      ALTER TABLE opponents RENAME TO _legacy_opponents_v1;
    END IF;
  END
  $$`,

  // Migration 2: Players (identity only)
  `CREATE TABLE IF NOT EXISTS players (
    player_id    TEXT PRIMARY KEY,
    screen_name  TEXT NOT NULL,
    platform     TEXT DEFAULT 'pppoker',
    player_type  TEXT DEFAULT 'unknown',
    notes        TEXT DEFAULT '',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_seen    TIMESTAMPTZ DEFAULT NOW()
  )`,

  // Migration 3: Player stats — VARIANT-ISOLATED
  `CREATE TABLE IF NOT EXISTS player_stats (
    stat_id           SERIAL PRIMARY KEY,
    player_id         TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    game_variant      TEXT NOT NULL,          -- 'PLO5' or 'PLO6'

    -- Sample size (trust gate)
    hands_played      INTEGER DEFAULT 0,

    -- Pre-flop (fractions, not percentages)
    vpip_count        INTEGER DEFAULT 0,
    pfr_count         INTEGER DEFAULT 0,
    three_bet_count   INTEGER DEFAULT 0,
    three_bet_opp     INTEGER DEFAULT 0,

    -- Post-flop
    cbet_flop_count   INTEGER DEFAULT 0,
    cbet_flop_opp     INTEGER DEFAULT 0,
    fold_to_cbet_count INTEGER DEFAULT 0,
    fold_to_cbet_opp  INTEGER DEFAULT 0,

    -- Showdown
    wtsd_count        INTEGER DEFAULT 0,
    wtsd_opp          INTEGER DEFAULT 0,
    wsd_count         INTEGER DEFAULT 0,

    -- Aggression
    total_bets        INTEGER DEFAULT 0,
    total_raises      INTEGER DEFAULT 0,
    total_calls       INTEGER DEFAULT 0,

    -- Sizing
    bet_size_sum      REAL DEFAULT 0.0,
    bet_size_count    INTEGER DEFAULT 0,

    UNIQUE(player_id, game_variant)
  )`,

  // Migration 4: Hand history (immutable action log)
  `CREATE TABLE IF NOT EXISTS hand_history (
    id               SERIAL PRIMARY KEY,
    hand_number      BIGINT NOT NULL,
    session_id       TEXT NOT NULL,
    player_id        TEXT NOT NULL REFERENCES players(player_id),
    game_variant     TEXT NOT NULL,
    street           TEXT NOT NULL,
    action           TEXT NOT NULL,
    amount           REAL DEFAULT 0,
    pot_size         REAL DEFAULT 0,
    is_voluntary     BOOLEAN DEFAULT FALSE,
    is_pfr           BOOLEAN DEFAULT FALSE,
    is_3bet_spot     BOOLEAN DEFAULT FALSE,
    is_cbet_spot     BOOLEAN DEFAULT FALSE,
    hero_cards       INTEGER[],
    board_cards      INTEGER[],
    equity_at_decision REAL,
    created_at       TIMESTAMPTZ DEFAULT NOW()
  )`,

  // Migration 5: High-performance indexes
  `CREATE INDEX IF NOT EXISTS idx_ps_lookup
   ON player_stats(player_id, game_variant)`,

  `CREATE INDEX IF NOT EXISTS idx_players_last_seen
   ON players(last_seen DESC)`,

  `CREATE INDEX IF NOT EXISTS idx_hh_player_variant
   ON hand_history(player_id, game_variant)`,

  `CREATE INDEX IF NOT EXISTS idx_hh_session
   ON hand_history(session_id)`,

  `CREATE INDEX IF NOT EXISTS idx_hh_created
   ON hand_history(created_at DESC)`,

  // Migration 6: Schema version tracker
  `CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
  )`,
];

// ── PgStore Class ───────────────────────────────────────────────────

class PgStore {
  constructor() {
    this._pool = null;
  }

  /**
   * Initialize the connection pool and run migrations.
   */
  async init() {
    this._pool = new Pool({
      host: process.env.PG_HOST || "localhost",
      port: parseInt(process.env.PG_PORT || "5432", 10),
      database: process.env.PG_DATABASE || "titan",
      user: process.env.PG_USER || "titan",
      password: process.env.PG_PASSWORD || "titan",
      max: parseInt(process.env.PG_POOL_MAX || "10", 10),
      idleTimeoutMillis: 30_000,
      connectionTimeoutMillis: 5_000,
    });

    // Test connection
    try {
      const client = await this._pool.connect();
      const { rows } = await client.query("SELECT NOW() as now");
      log.info({ serverTime: rows[0].now }, "PostgreSQL connected");
      client.release();
    } catch (err) {
      log.warn(
        { err: err.message },
        "PostgreSQL not available — profiling disabled",
      );
      this._pool = null;
      return;
    }

    // Run migrations sequentially
    for (const sql of MIGRATIONS) {
      try {
        await this._pool.query(sql);
      } catch (err) {
        log.error({ err, sql: sql.slice(0, 80) }, "Migration failed");
      }
    }

    // Set schema version
    await this._pool.query(
      `INSERT INTO schema_meta (key, value) VALUES ('schema_version', '2')
       ON CONFLICT (key) DO UPDATE SET value = '2'`,
    );

    log.info("Migrations complete — schema v2 (variant-isolated)");
  }

  // ─── Player Identity ─────────────────────────────────────

  /**
   * Create or update a player's identity.
   */
  async touchPlayer(playerId, screenName, platform = "pppoker") {
    if (!this._pool) return;

    await this._pool.query(
      `INSERT INTO players (player_id, screen_name, platform)
       VALUES ($1, $2, $3)
       ON CONFLICT (player_id) DO UPDATE SET
         screen_name = EXCLUDED.screen_name,
         last_seen   = NOW()`,
      [playerId, screenName, platform],
    );
  }

  /**
   * Ensure a stats row exists for the given player + variant.
   */
  async ensureStats(playerId, variant) {
    if (!this._pool) return;

    await this._pool.query(
      `INSERT INTO player_stats (player_id, game_variant)
       VALUES ($1, $2)
       ON CONFLICT (player_id, game_variant) DO NOTHING`,
      [playerId, variant],
    );
  }

  // ─── Profile Queries ─────────────────────────────────────

  /**
   * Get a player's computed profile for a specific variant.
   * Applies the TRUST GATE: returns { trusted: false } if
   * hands_played < MIN_TRUST_HANDS.
   *
   * @param {string} playerId
   * @param {string} variant — 'PLO5' | 'PLO6'
   * @returns {Object|null}
   */
  async getProfile(playerId, variant = "PLO5") {
    if (!this._pool) return null;

    const { rows } = await this._pool.query(
      `SELECT ps.*, p.screen_name, p.player_type, p.notes, p.last_seen
       FROM   player_stats ps
       JOIN   players p ON p.player_id = ps.player_id
       WHERE  ps.player_id = $1 AND ps.game_variant = $2`,
      [playerId, variant],
    );

    if (!rows[0]) return null;
    return this._buildProfile(rows[0], variant);
  }

  /**
   * Get profiles for multiple opponents in a specific variant.
   *
   * @param {string[]} playerIds
   * @param {string} variant
   * @returns {Object[]}
   */
  async getProfiles(playerIds, variant = "PLO5") {
    if (!this._pool || !playerIds?.length) return [];

    try {
      const { rows } = await this._pool.query(
        `SELECT ps.*, p.screen_name, p.player_type, p.notes, p.last_seen
         FROM   player_stats ps
         JOIN   players p ON p.player_id = ps.player_id
         WHERE  ps.player_id = ANY($1) AND ps.game_variant = $2`,
        [playerIds, variant],
      );

      return rows.map((row) => this._buildProfile(row, variant));
    } catch (err) {
      log.error({ err }, "getProfiles failed");
      return [];
    }
  }

  /**
   * Get both PLO5 and PLO6 profiles for a single player.
   */
  async getAllVariants(playerId) {
    const [plo5, plo6] = await Promise.all([
      this.getProfile(playerId, "PLO5"),
      this.getProfile(playerId, "PLO6"),
    ]);
    return { PLO5: plo5, PLO6: plo6 };
  }

  /**
   * Build a profile object from a raw DB row.
   * Computes percentages from counts ON THE FLY.
   * @private
   */
  _buildProfile(row, variant) {
    const h = row.hands_played;
    const trusted = h >= MIN_TRUST_HANDS;

    const pct = (count, opp) =>
      opp > 0 ? Math.round((count / opp) * 1000) / 10 : 0;

    const af =
      row.total_calls > 0
        ? Math.round(
            ((row.total_bets + row.total_raises) / row.total_calls) * 100,
          ) / 100
        : 0;

    const avgSizing =
      row.bet_size_count > 0
        ? Math.round((row.bet_size_sum / row.bet_size_count) * 100) / 100
        : 0;

    return {
      player_id: row.player_id,
      screen_name: row.screen_name,
      player_type: row.player_type,
      archetype: row.player_type, // alias for proto compat
      game_variant: variant,
      trusted,
      hands_played: h,

      // Computed percentages
      vpip: pct(row.vpip_count, h),
      pfr: pct(row.pfr_count, h),
      three_bet: pct(row.three_bet_count, row.three_bet_opp),
      cbet_flop: pct(row.cbet_flop_count, row.cbet_flop_opp),
      fold_to_cbet: pct(row.fold_to_cbet_count, row.fold_to_cbet_opp),
      wtsd: pct(row.wtsd_count, row.wtsd_opp),
      wsd: pct(row.wsd_count, row.wtsd_count),
      af,
      aggression_factor: af, // proto field name
      avg_sizing: avgSizing,

      // Raw counters for debugging / proto serialization
      raw: {
        vpip_count: row.vpip_count,
        pfr_count: row.pfr_count,
        three_bet_count: row.three_bet_count,
        three_bet_opp: row.three_bet_opp,
        cbet_flop_count: row.cbet_flop_count,
        cbet_flop_opp: row.cbet_flop_opp,
        fold_to_cbet_count: row.fold_to_cbet_count,
        fold_to_cbet_opp: row.fold_to_cbet_opp,
        wtsd_count: row.wtsd_count,
        wtsd_opp: row.wtsd_opp,
        wsd_count: row.wsd_count,
        total_bets: row.total_bets,
        total_raises: row.total_raises,
        total_calls: row.total_calls,
        bet_size_sum: row.bet_size_sum,
        bet_size_count: row.bet_size_count,
      },

      notes: row.notes,
      last_seen: row.last_seen,
    };
  }

  // ─── Hand Recording (Atomic Transaction) ─────────────────

  /**
   * Process a complete hand result — increment stats + log actions.
   * All database writes are wrapped in a single transaction.
   *
   * @param {Object} handData
   * @param {string} handData.player_id
   * @param {string} handData.screen_name
   * @param {string} handData.game_variant — 'PLO5' | 'PLO6'
   * @param {string} handData.session_id
   * @param {number} handData.hand_number
   * @param {boolean} handData.voluntary
   * @param {boolean} handData.raisedPreflop
   * @param {boolean} [handData.had3BetOpp]
   * @param {boolean} [handData.did3Bet]
   * @param {boolean} [handData.hadCbetOpp]
   * @param {boolean} [handData.didCbet]
   * @param {boolean} [handData.facedCbet]
   * @param {boolean} [handData.foldedToCbet]
   * @param {boolean} [handData.sawRiver]
   * @param {boolean} [handData.wentToShowdown]
   * @param {boolean} [handData.wonAtShowdown]
   * @param {Object[]} [handData.actions] — [{street, action, amount, potSize, potRatio}]
   */
  async recordHand(handData) {
    if (!this._pool) return;

    const client = await this._pool.connect();
    const v = handData.game_variant || "PLO5";

    try {
      await client.query("BEGIN");

      // 1. Upsert player identity
      await client.query(
        `INSERT INTO players (player_id, screen_name)
         VALUES ($1, $2)
         ON CONFLICT (player_id) DO UPDATE SET
           screen_name = EXCLUDED.screen_name,
           last_seen   = NOW()`,
        [handData.player_id, handData.screen_name || handData.player_id],
      );

      // 2. Ensure stats row exists
      await client.query(
        `INSERT INTO player_stats (player_id, game_variant)
         VALUES ($1, $2)
         ON CONFLICT (player_id, game_variant) DO NOTHING`,
        [handData.player_id, v],
      );

      // 3. Increment counters atomically
      const incs = ["hands_played = hands_played + 1"];
      if (handData.voluntary) incs.push("vpip_count = vpip_count + 1");
      if (handData.raisedPreflop) incs.push("pfr_count = pfr_count + 1");

      // 3-Bet
      if (handData.had3BetOpp) {
        incs.push("three_bet_opp = three_bet_opp + 1");
        if (handData.did3Bet)
          incs.push("three_bet_count = three_bet_count + 1");
      }

      // C-Bet
      if (handData.hadCbetOpp) {
        incs.push("cbet_flop_opp = cbet_flop_opp + 1");
        if (handData.didCbet)
          incs.push("cbet_flop_count = cbet_flop_count + 1");
      }

      // Fold to C-Bet
      if (handData.facedCbet) {
        incs.push("fold_to_cbet_opp = fold_to_cbet_opp + 1");
        if (handData.foldedToCbet)
          incs.push("fold_to_cbet_count = fold_to_cbet_count + 1");
      }

      // Showdown
      if (handData.sawRiver) {
        incs.push("wtsd_opp = wtsd_opp + 1");
        if (handData.wentToShowdown) {
          incs.push("wtsd_count = wtsd_count + 1");
          if (handData.wonAtShowdown) incs.push("wsd_count = wsd_count + 1");
        }
      }

      // Aggression
      let bets = 0,
        raises = 0,
        calls = 0,
        sizingSum = 0,
        sizingN = 0;
      if (handData.actions) {
        for (const act of handData.actions) {
          if (act.action === "bet") bets++;
          else if (act.action === "raise") raises++;
          else if (act.action === "call") calls++;

          if (act.potRatio != null && act.action !== "call") {
            sizingSum += act.potRatio;
            sizingN++;
          }
        }
      }
      if (bets) incs.push(`total_bets = total_bets + ${bets}`);
      if (raises) incs.push(`total_raises = total_raises + ${raises}`);
      if (calls) incs.push(`total_calls = total_calls + ${calls}`);
      if (sizingN) {
        incs.push(`bet_size_sum = bet_size_sum + ${sizingSum}`);
        incs.push(`bet_size_count = bet_size_count + ${sizingN}`);
      }

      await client.query(
        `UPDATE player_stats SET ${incs.join(", ")}
         WHERE  player_id = $1 AND game_variant = $2`,
        [handData.player_id, v],
      );

      // 4. Log individual actions to hand_history
      if (handData.actions) {
        for (const act of handData.actions) {
          await client.query(
            `INSERT INTO hand_history
               (hand_number, session_id, player_id, game_variant,
                street, action, amount, pot_size,
                is_voluntary, is_pfr, is_3bet_spot, is_cbet_spot,
                hero_cards, board_cards, equity_at_decision)
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
            [
              handData.hand_number,
              handData.session_id,
              handData.player_id,
              v,
              act.street || "unknown",
              act.action || "unknown",
              act.amount || 0,
              act.potSize || 0,
              act.isVoluntary || false,
              act.isPfr || false,
              act.is3BetSpot || false,
              act.isCbetSpot || false,
              handData.hero_cards || [],
              handData.board_cards || [],
              act.equity || 0,
            ],
          );
        }
      }

      await client.query("COMMIT");
    } catch (err) {
      await client.query("ROLLBACK");
      log.error({ err }, "recordHand failed");
      throw err;
    } finally {
      client.release();
    }
  }

  // ─── Listing / Leaderboard ────────────────────────────────

  /**
   * Get all opponents for a given variant, sorted by hands played.
   *
   * @param {string} variant
   * @param {number} [limit=50]
   * @returns {Object[]}
   */
  async listAll(variant = "PLO5", limit = 50) {
    if (!this._pool) return [];

    const { rows } = await this._pool.query(
      `SELECT ps.*, p.screen_name, p.player_type, p.notes, p.last_seen
       FROM   player_stats ps
       JOIN   players p ON p.player_id = ps.player_id
       WHERE  ps.game_variant = $1
       ORDER  BY ps.hands_played DESC
       LIMIT  $2`,
      [variant, limit],
    );

    return rows.map((row) => this._buildProfile(row, variant));
  }

  // ─── Lifecycle ────────────────────────────────────────────

  /**
   * Close the connection pool.
   */
  async close() {
    if (this._pool) {
      await this._pool.end();
      log.info("PostgreSQL pool closed");
    }
  }
}

module.exports = { PgStore, MIN_TRUST_HANDS };

/**
 * Opponent Database — SQLite-backed player profiling for Omaha.
 *
 * ═══════════════════════════════════════════════════════════════
 * CRITICAL DESIGN: Stats are VARIANT-ISOLATED (PLO5 vs PLO6).
 * ═══════════════════════════════════════════════════════════════
 *
 * In Hold'em, VPIP 30% = loose.  In PLO6 with 6 cards, VPIP 65%
 * is NORMAL because players always "connect with something".
 * Mixing PLO5/PLO6 stats corrupts opponent classification.
 *
 * Stats are stored as FRACTIONS (count/opportunities), not
 * percentages.  This prevents the "1-hand maniac" illusion:
 *   ❌  vpip = 100%  (after 1 hand — misleading)
 *   ✅  vpip_count = 1, hands_played = 1  (clearly insufficient)
 *
 * Trust Gate: The engine ignores profiles with < MIN_TRUST_HANDS
 * and defaults to GTO.  Fraction storage makes this trivial.
 *
 * Uses better-sqlite3 for synchronous I/O (no async overhead in
 * the Electron main process for small reads/writes).
 */

"use strict";

const path = require("node:path");

/**
 * Schema version — bump when altering table structure.
 */
const SCHEMA_VERSION = 2;

/**
 * Minimum hands before the engine trusts profiling data.
 * Below this threshold → pure GTO (no exploit adjustments).
 */
const MIN_TRUST_HANDS = 50;

/**
 * Variant-specific thresholds for opponent classification.
 * These are the calibrated ranges per variant — the core of
 * the Ponto Cego fix.
 *
 * In PLO5, ranges are slightly looser than NLHE but far tighter
 * than PLO6.  In PLO6 (Araguaína tables), VPIP 60-70% is common
 * because 6-card hands always "hit something".
 */
const VARIANT_THRESHOLDS = {
  PLO5: {
    vpip_tight: 30, // Below this = Nit  (HE would be ~20)
    vpip_loose: 55, // Above this = Loose
    pfr_passive: 12,
    pfr_agg: 30,
    af_passive: 1.3,
    af_agg: 3.0,
    wtsd_low: 25,
    wtsd_high: 38,
    cbet_low: 30,
    cbet_high: 65,
  },
  PLO6: {
    vpip_tight: 40, // PLO6 Nit plays < 40% — insanely tight
    vpip_loose: 70, // 70%+ in PLO6 = genuine whale/maniac
    pfr_passive: 15,
    pfr_agg: 35,
    af_passive: 1.2,
    af_agg: 2.8,
    wtsd_low: 28,
    wtsd_high: 42,
    cbet_low: 25,
    cbet_high: 55,
  },
};

const CREATE_TABLE_SQL = `
-- ═══ 1. Players (Identity) ═══════════════════════════════════
CREATE TABLE IF NOT EXISTS players (
  player_id    TEXT PRIMARY KEY,
  screen_name  TEXT NOT NULL,
  player_type  TEXT DEFAULT 'unknown',
  notes        TEXT,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_seen    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══ 2. Player Stats — VARIANT-ISOLATED ══════════════════════
-- NEVER mix PLO5 and PLO6 stats.  Same player gets 2 rows.
CREATE TABLE IF NOT EXISTS player_stats (
  stat_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  player_id         TEXT NOT NULL,
  game_variant      TEXT NOT NULL,          -- 'PLO5' or 'PLO6'

  -- Sample size (trust gate)
  hands_played      INTEGER DEFAULT 0,

  -- Pre-flop tendencies (fractions, not percentages)
  vpip_count        INTEGER DEFAULT 0,      -- Voluntarily put $ in
  pfr_count         INTEGER DEFAULT 0,      -- Pre-flop raise
  three_bet_count   INTEGER DEFAULT 0,      -- 3-bet (re-raise)
  three_bet_opp     INTEGER DEFAULT 0,      -- 3-bet opportunities

  -- Post-flop tendencies
  cbet_flop_count   INTEGER DEFAULT 0,      -- C-bet fired on flop
  cbet_flop_opp     INTEGER DEFAULT 0,      -- C-bet opportunities
  fold_to_cbet_count INTEGER DEFAULT 0,     -- Folded to c-bet
  fold_to_cbet_opp  INTEGER DEFAULT 0,      -- Faced c-bet

  -- Showdown tendencies
  wtsd_count        INTEGER DEFAULT 0,      -- Went to Showdown
  wtsd_opp          INTEGER DEFAULT 0,      -- Saw river (SD opportunity)
  wsd_count         INTEGER DEFAULT 0,      -- Won $ at Showdown

  -- Aggression counters (for AF calculation)
  total_bets        INTEGER DEFAULT 0,
  total_raises      INTEGER DEFAULT 0,
  total_calls       INTEGER DEFAULT 0,

  -- Sizing tendencies
  bet_size_sum      REAL DEFAULT 0.0,       -- Sum of bet/pot ratios
  bet_size_count    INTEGER DEFAULT 0,      -- Number of sizing samples

  FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE,
  UNIQUE(player_id, game_variant)
);

-- ═══ 3. Hand History (Immutable Action Log) ══════════════════
CREATE TABLE IF NOT EXISTS hand_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  player_id     TEXT NOT NULL,
  game_variant  TEXT NOT NULL,              -- 'PLO5' or 'PLO6'
  hand_num      INTEGER,
  street        TEXT,
  action        TEXT,
  amount        REAL DEFAULT 0,
  pot_size      REAL DEFAULT 0,
  is_voluntary  INTEGER DEFAULT 0,         -- 1 if voluntary (not BB check)
  is_pfr        INTEGER DEFAULT 0,         -- 1 if preflop raise
  is_3bet_spot  INTEGER DEFAULT 0,
  is_cbet_spot  INTEGER DEFAULT 0,
  timestamp     TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (player_id) REFERENCES players(player_id)
);

-- ═══ 4. High-Performance Indexes ═════════════════════════════
-- The MCP/LLM advisor and gRPC both query by (player_id, variant).
CREATE INDEX IF NOT EXISTS idx_player_stats_lookup
  ON player_stats(player_id, game_variant);
CREATE INDEX IF NOT EXISTS idx_players_last_seen
  ON players(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_hh_player
  ON hand_history(player_id, game_variant);
CREATE INDEX IF NOT EXISTS idx_hh_hand
  ON hand_history(hand_num);

-- ═══ 5. Schema Meta ══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
`;

class OpponentDb {
  /**
   * @param {string} [dbPath] - Path to SQLite file (default: db/opponents.db)
   */
  constructor(dbPath = null) {
    this._dbPath = dbPath || path.join(process.cwd(), "db", "opponents.db");
    this._db = null;
    /** @type {Map<string, import("better-sqlite3").Statement>} */
    this._stmts = new Map();
  }

  // ─── Lifecycle ────────────────────────────────────────────

  /**
   * Open (or create) the database and ensure schema is up to date.
   * @returns {OpponentDb}
   */
  init() {
    const Database = require("better-sqlite3");
    const fs = require("node:fs");

    const dir = path.dirname(this._dbPath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

    this._db = new Database(this._dbPath);
    this._db.pragma("journal_mode = WAL");
    this._db.pragma("synchronous = NORMAL");
    this._db.pragma("foreign_keys = ON");

    // Run migration if upgrading from v1
    this._migrate();

    this._db.exec(CREATE_TABLE_SQL);
    this._setVersion(SCHEMA_VERSION);
    this._prepareStatements();

    return this;
  }

  /** Close the database connection. */
  close() {
    if (this._db) {
      this._db.close();
      this._db = null;
      this._stmts.clear();
    }
  }

  // ─── Migration v1 → v2 ───────────────────────────────────

  /** @private */
  _migrate() {
    const existing = this._getVersion();
    if (existing && existing < 2) {
      // v1 had a flat `opponents` table — archive it, fresh start
      const tables = this._db
        .prepare(
          "SELECT name FROM sqlite_master WHERE type='table' AND name='opponents'",
        )
        .get();
      if (tables) {
        this._db.exec("ALTER TABLE opponents RENAME TO _legacy_opponents_v1");
      }
    }
  }

  /** @private */
  _getVersion() {
    try {
      const row = this._db
        .prepare("SELECT value FROM schema_meta WHERE key = 'schema_version'")
        .get();
      return row ? Number(row.value) : null;
    } catch {
      return null; // table doesn't exist yet
    }
  }

  /** @private */
  _setVersion(v) {
    this._db
      .prepare(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
      )
      .run(String(v));
  }

  // ─── Prepared Statements ──────────────────────────────────

  /** @private */
  _prepareStatements() {
    const db = this._db;

    this._stmts.set(
      "upsertPlayer",
      db.prepare(`
        INSERT INTO players (player_id, screen_name)
        VALUES (?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
          screen_name = excluded.screen_name,
          last_seen   = datetime('now')
      `),
    );

    this._stmts.set(
      "ensureStats",
      db.prepare(`
        INSERT OR IGNORE INTO player_stats (player_id, game_variant)
        VALUES (?, ?)
      `),
    );

    this._stmts.set(
      "getStats",
      db.prepare(`
        SELECT ps.*, p.screen_name, p.player_type, p.notes,
               p.created_at AS player_created, p.last_seen
        FROM   player_stats ps
        JOIN   players p ON p.player_id = ps.player_id
        WHERE  ps.player_id = ? AND ps.game_variant = ?
      `),
    );

    this._stmts.set(
      "getPlayer",
      db.prepare("SELECT * FROM players WHERE player_id = ?"),
    );

    this._stmts.set(
      "getAllVariantStats",
      db.prepare(`
        SELECT ps.*, p.screen_name, p.player_type, p.notes, p.last_seen
        FROM   player_stats ps
        JOIN   players p ON p.player_id = ps.player_id
        WHERE  ps.game_variant = ?
        ORDER  BY ps.hands_played DESC
        LIMIT  ?
      `),
    );

    // ── Increment counters (atomic, single-column updates) ──

    this._stmts.set(
      "incHand",
      db.prepare(`
        UPDATE player_stats
        SET    hands_played = hands_played + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incVpip",
      db.prepare(`
        UPDATE player_stats
        SET    vpip_count = vpip_count + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incPfr",
      db.prepare(`
        UPDATE player_stats
        SET    pfr_count = pfr_count + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "inc3bet",
      db.prepare(`
        UPDATE player_stats
        SET    three_bet_count = three_bet_count + 1,
               three_bet_opp  = three_bet_opp  + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "inc3betOppOnly",
      db.prepare(`
        UPDATE player_stats
        SET    three_bet_opp = three_bet_opp + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incCbet",
      db.prepare(`
        UPDATE player_stats
        SET    cbet_flop_count = cbet_flop_count + 1,
               cbet_flop_opp  = cbet_flop_opp  + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incCbetOppOnly",
      db.prepare(`
        UPDATE player_stats
        SET    cbet_flop_opp = cbet_flop_opp + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incFoldToCbet",
      db.prepare(`
        UPDATE player_stats
        SET    fold_to_cbet_count = fold_to_cbet_count + 1,
               fold_to_cbet_opp  = fold_to_cbet_opp  + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incFoldToCbetOppOnly",
      db.prepare(`
        UPDATE player_stats
        SET    fold_to_cbet_opp = fold_to_cbet_opp + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incWtsd",
      db.prepare(`
        UPDATE player_stats
        SET    wtsd_count = wtsd_count + 1,
               wtsd_opp   = wtsd_opp   + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incWtsdOppOnly",
      db.prepare(`
        UPDATE player_stats
        SET    wtsd_opp = wtsd_opp + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incWsd",
      db.prepare(`
        UPDATE player_stats
        SET    wsd_count = wsd_count + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incAggBet",
      db.prepare(`
        UPDATE player_stats
        SET    total_bets = total_bets + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incAggRaise",
      db.prepare(`
        UPDATE player_stats
        SET    total_raises = total_raises + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "incAggCall",
      db.prepare(`
        UPDATE player_stats
        SET    total_calls = total_calls + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "addSizing",
      db.prepare(`
        UPDATE player_stats
        SET    bet_size_sum   = bet_size_sum  + ?,
               bet_size_count = bet_size_count + 1
        WHERE  player_id = ? AND game_variant = ?
      `),
    );

    this._stmts.set(
      "updatePlayerType",
      db.prepare("UPDATE players SET player_type = ? WHERE player_id = ?"),
    );

    this._stmts.set(
      "updateNotes",
      db.prepare("UPDATE players SET notes = ? WHERE player_id = ?"),
    );

    this._stmts.set(
      "recordAction",
      db.prepare(`
        INSERT INTO hand_history
          (player_id, game_variant, hand_num, street, action, amount,
           pot_size, is_voluntary, is_pfr, is_3bet_spot, is_cbet_spot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `),
    );

    this._stmts.set(
      "getHistory",
      db.prepare(`
        SELECT * FROM hand_history
        WHERE  player_id = ? AND game_variant = ?
        ORDER  BY id DESC LIMIT ?
      `),
    );
  }

  // ─── Player Identity ─────────────────────────────────────

  /**
   * Create or update a player's identity (name only).
   * Stats are updated through increment methods, never directly.
   *
   * @param {string} playerId   Unique ID (PPPoker club id / screen grab hash)
   * @param {string} screenName Display name read from OCR
   */
  touchPlayer(playerId, screenName) {
    this._stmts.get("upsertPlayer").run(playerId, screenName);
  }

  /**
   * Get raw player identity (without stats).
   * @param {string} playerId
   * @returns {Object|null}
   */
  getPlayer(playerId) {
    return this._stmts.get("getPlayer").get(playerId) || null;
  }

  /**
   * Set the player's archetype label.
   * @param {string} playerId
   * @param {string} playerType — e.g. 'fish', 'nit', 'lag', 'tag', 'whale'
   */
  setPlayerType(playerId, playerType) {
    this._stmts.get("updatePlayerType").run(playerType, playerId);
  }

  /**
   * Set notes for a player.
   * @param {string} playerId
   * @param {string} notes
   */
  setNotes(playerId, notes) {
    this._stmts.get("updateNotes").run(notes, playerId);
  }

  // ─── Stats: Ensure + Read ────────────────────────────────

  /**
   * Ensure a stats row exists for the given player + variant.
   * Idempotent — safe to call on every hand.
   *
   * @param {string} playerId
   * @param {string} variant — 'PLO5' | 'PLO6'
   */
  ensureStats(playerId, variant) {
    this._stmts.get("ensureStats").run(playerId, variant);
  }

  /**
   * Get a player's computed profile for a specific variant.
   *
   * The TRUST GATE is applied here:
   *   • If hands_played < MIN_TRUST_HANDS → returns { trusted: false }
   *   • Otherwise → returns full profile with computed percentages
   *
   * The engine should check `profile.trusted` before using exploitative
   * adjustments.  If untrusted → play GTO.
   *
   * @param {string} playerId
   * @param {string} variant — 'PLO5' | 'PLO6'
   * @returns {Object|null} — null if player doesn't exist at all
   */
  getProfile(playerId, variant) {
    const row = this._stmts.get("getStats").get(playerId, variant);
    if (!row) return null;

    const h = row.hands_played;
    const trusted = h >= MIN_TRUST_HANDS;

    // Even if untrusted, return the raw counts so the UI can show
    // "warming up" indicators.  The engine uses `trusted` flag.
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

    const profile = {
      player_id: row.player_id,
      screen_name: row.screen_name,
      player_type: row.player_type,
      archetype: row.player_type, // alias for proto compat
      game_variant: variant,
      trusted,
      hands_played: h,

      // Computed percentages (from fractions)
      vpip: pct(row.vpip_count, h),
      pfr: pct(row.pfr_count, h),
      three_bet: pct(row.three_bet_count, row.three_bet_opp),
      cbet_flop: pct(row.cbet_flop_count, row.cbet_flop_opp),
      fold_to_cbet: pct(row.fold_to_cbet_count, row.fold_to_cbet_opp),
      wtsd: pct(row.wtsd_count, row.wtsd_opp),
      wsd: pct(row.wsd_count, row.wtsd_count), // WSD% = won / went to SD
      af,
      aggression_factor: af, // proto field name
      avg_sizing: avgSizing,

      // Raw counters (for UI / debugging)
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

      // Notes / meta
      notes: row.notes,
      last_seen: row.last_seen,
    };

    // Auto-classify if trusted
    if (trusted) {
      profile.player_type = this.classify(profile);
      profile.archetype = profile.player_type;
      this._stmts.get("updatePlayerType").run(profile.player_type, playerId);
    }

    return profile;
  }

  // ─── Stat Increments (Atomic) ────────────────────────────
  //
  // Each method increments EXACTLY the relevant counter(s).
  // Call these from the hand-replay / action parser.
  //
  // The pattern is:
  //   1. touchPlayer(id, name)       — identity
  //   2. ensureStats(id, variant)    — guarantee row
  //   3. recordHandPlayed(id, var)   — +1 hands_played
  //   4. recordVpip/Pfr/…            — +1 specific counter
  //

  /** +1 hand played for this variant */
  recordHandPlayed(playerId, variant) {
    this._stmts.get("incHand").run(playerId, variant);
  }

  /** Player voluntarily put money in pot */
  recordVpip(playerId, variant) {
    this._stmts.get("incVpip").run(playerId, variant);
  }

  /** Player raised preflop */
  recordPfr(playerId, variant) {
    this._stmts.get("incPfr").run(playerId, variant);
  }

  /** Player 3-bet (had the opportunity AND did it) */
  record3Bet(playerId, variant) {
    this._stmts.get("inc3bet").run(playerId, variant);
  }

  /** Player had 3-bet opportunity but did NOT 3-bet */
  record3BetOpp(playerId, variant) {
    this._stmts.get("inc3betOppOnly").run(playerId, variant);
  }

  /** Player continuation-bet on the flop (opportunity AND bet) */
  recordCbet(playerId, variant) {
    this._stmts.get("incCbet").run(playerId, variant);
  }

  /** Player had c-bet opportunity but checked */
  recordCbetOpp(playerId, variant) {
    this._stmts.get("incCbetOppOnly").run(playerId, variant);
  }

  /** Player folded to a c-bet (opportunity AND folded) */
  recordFoldToCbet(playerId, variant) {
    this._stmts.get("incFoldToCbet").run(playerId, variant);
  }

  /** Player faced c-bet but did NOT fold */
  recordFoldToCbetOpp(playerId, variant) {
    this._stmts.get("incFoldToCbetOppOnly").run(playerId, variant);
  }

  /** Player went to showdown (opportunity AND went) */
  recordWtsd(playerId, variant) {
    this._stmts.get("incWtsd").run(playerId, variant);
  }

  /** Player saw the river but folded (didn't go to SD) */
  recordWtsdOpp(playerId, variant) {
    this._stmts.get("incWtsdOppOnly").run(playerId, variant);
  }

  /** Player won at showdown */
  recordWsd(playerId, variant) {
    this._stmts.get("incWsd").run(playerId, variant);
  }

  /** Player made a bet (postflop, for AF calc) */
  recordBet(playerId, variant) {
    this._stmts.get("incAggBet").run(playerId, variant);
  }

  /** Player made a raise (postflop, for AF calc) */
  recordRaise(playerId, variant) {
    this._stmts.get("incAggRaise").run(playerId, variant);
  }

  /** Player called (postflop, for AF calc) */
  recordCall(playerId, variant) {
    this._stmts.get("incAggCall").run(playerId, variant);
  }

  /**
   * Record a bet sizing sample (potRatio = betSize / potSize).
   * @param {string} playerId
   * @param {string} variant
   * @param {number} potRatio
   */
  recordSizing(playerId, variant, potRatio) {
    this._stmts.get("addSizing").run(potRatio, playerId, variant);
  }

  // ─── Batch Hand Processing ───────────────────────────────

  /**
   * Process an entire hand's worth of actions in a single
   * transaction.  This is the main entry-point from the
   * hand-replay engine.
   *
   * @param {string} playerId
   * @param {string} screenName
   * @param {string} variant — 'PLO5' | 'PLO6'
   * @param {Object} handSummary — Pre-parsed hand summary
   * @param {boolean} handSummary.voluntary — Did player VPIP?
   * @param {boolean} handSummary.raisedPreflop — PFR?
   * @param {boolean} [handSummary.had3BetOpp]
   * @param {boolean} [handSummary.did3Bet]
   * @param {boolean} [handSummary.hadCbetOpp]
   * @param {boolean} [handSummary.didCbet]
   * @param {boolean} [handSummary.facedCbet]
   * @param {boolean} [handSummary.foldedToCbet]
   * @param {boolean} [handSummary.sawRiver]
   * @param {boolean} [handSummary.wentToShowdown]
   * @param {boolean} [handSummary.wonAtShowdown]
   * @param {Object[]} [handSummary.postflopActions] — {type:'bet'|'raise'|'call', potRatio?}
   */
  processHand(playerId, screenName, variant, handSummary) {
    const run = this._db.transaction(() => {
      this.touchPlayer(playerId, screenName);
      this.ensureStats(playerId, variant);
      this.recordHandPlayed(playerId, variant);

      if (handSummary.voluntary) this.recordVpip(playerId, variant);
      if (handSummary.raisedPreflop) this.recordPfr(playerId, variant);

      // 3-Bet
      if (handSummary.had3BetOpp) {
        if (handSummary.did3Bet) this.record3Bet(playerId, variant);
        else this.record3BetOpp(playerId, variant);
      }

      // C-Bet
      if (handSummary.hadCbetOpp) {
        if (handSummary.didCbet) this.recordCbet(playerId, variant);
        else this.recordCbetOpp(playerId, variant);
      }

      // Fold to C-Bet
      if (handSummary.facedCbet) {
        if (handSummary.foldedToCbet) this.recordFoldToCbet(playerId, variant);
        else this.recordFoldToCbetOpp(playerId, variant);
      }

      // Showdown
      if (handSummary.sawRiver) {
        if (handSummary.wentToShowdown) {
          this.recordWtsd(playerId, variant);
          if (handSummary.wonAtShowdown) this.recordWsd(playerId, variant);
        } else {
          this.recordWtsdOpp(playerId, variant);
        }
      }

      // Postflop aggression + sizing
      if (handSummary.postflopActions) {
        for (const act of handSummary.postflopActions) {
          if (act.type === "bet") this.recordBet(playerId, variant);
          else if (act.type === "raise") this.recordRaise(playerId, variant);
          else if (act.type === "call") this.recordCall(playerId, variant);

          if (act.potRatio != null && act.type !== "call") {
            this.recordSizing(playerId, variant, act.potRatio);
          }
        }
      }
    });

    run();
  }

  // ─── Classification ──────────────────────────────────────

  /**
   * Classify opponent archetype using VARIANT-SPECIFIC thresholds.
   *
   * The Ponto Cego fix:  PLO5 and PLO6 have COMPLETELY different
   * threshold curves.  A PLO6 player with VPIP 60% is NORMAL.
   * The same player in PLO5 would be classified as a whale.
   *
   * @param {Object} profile — As returned by getProfile()
   * @returns {string} — 'whale'|'fish'|'nit'|'lag'|'tag'|'reg'|'unknown'
   */
  classify(profile) {
    if (!profile.trusted) return "unknown";

    const t =
      VARIANT_THRESHOLDS[profile.game_variant] || VARIANT_THRESHOLDS.PLO5;

    const { vpip, pfr, af } = profile;

    // Whale: absurdly loose + passive  (PLO6: VPIP > 70%)
    if (vpip > t.vpip_loose + 15) return "whale";

    // Fish: loose + passive OR high WTSD
    if (vpip > t.vpip_loose && af < t.af_agg) return "fish";

    // Nit: super tight
    if (vpip < t.vpip_tight && pfr < t.pfr_passive) return "nit";

    // LAG: loose + aggressive
    if (vpip > t.vpip_loose && af >= t.af_agg) return "lag";

    // TAG: tight-ish + aggressive
    if (vpip <= t.vpip_loose && pfr >= t.pfr_agg && af >= t.af_agg) {
      return "tag";
    }

    // Reg: balanced stats, probably a competent player
    if (
      vpip >= t.vpip_tight &&
      vpip <= t.vpip_loose &&
      af >= t.af_passive &&
      af <= t.af_agg
    ) {
      return "reg";
    }

    return "unknown";
  }

  // ─── Action Log ──────────────────────────────────────────

  /**
   * Record a raw action into hand_history (immutable log).
   *
   * @param {string} playerId
   * @param {string} variant
   * @param {Object} action
   */
  recordAction(playerId, variant, action) {
    this._stmts
      .get("recordAction")
      .run(
        playerId,
        variant,
        action.handNum || 0,
        action.street || "unknown",
        action.action || "unknown",
        action.amount || 0,
        action.potSize || 0,
        action.isVoluntary ? 1 : 0,
        action.isPfr ? 1 : 0,
        action.is3BetSpot ? 1 : 0,
        action.isCbetSpot ? 1 : 0,
      );
  }

  /**
   * Get recent hand history for a player in a specific variant.
   * @param {string} playerId
   * @param {string} variant
   * @param {number} [limit=100]
   * @returns {Object[]}
   */
  getHistory(playerId, variant, limit = 100) {
    return this._stmts.get("getHistory").all(playerId, variant, limit);
  }

  // ─── Listing / Query ─────────────────────────────────────

  /**
   * Get all opponent profiles for a given variant, sorted by hands.
   * @param {string} variant — 'PLO5' | 'PLO6'
   * @param {number} [limit=50]
   * @returns {Object[]} — Each has computed percentages + trusted flag
   */
  listAll(variant, limit = 50) {
    const rows = this._stmts.get("getAllVariantStats").all(variant, limit);
    return rows.map((row) => {
      const stub = { player_id: row.player_id, game_variant: variant };
      return this.getProfile(row.player_id, variant) || stub;
    });
  }

  /**
   * Get profiles for ALL variants of a single player.
   * Useful for the dashboard's player detail view.
   *
   * @param {string} playerId
   * @returns {{ PLO5: Object|null, PLO6: Object|null }}
   */
  getAllVariants(playerId) {
    return {
      PLO5: this.getProfile(playerId, "PLO5"),
      PLO6: this.getProfile(playerId, "PLO6"),
    };
  }

  /**
   * Get the thresholds table for a variant (for UI display / debugging).
   * @param {string} variant
   * @returns {Object}
   */
  getThresholds(variant) {
    return VARIANT_THRESHOLDS[variant] || VARIANT_THRESHOLDS.PLO5;
  }
}

module.exports = {
  OpponentDb,
  SCHEMA_VERSION,
  MIN_TRUST_HANDS,
  VARIANT_THRESHOLDS,
};

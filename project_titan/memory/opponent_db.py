"""Opponent profiling database — persistent HUD statistics.

Tracks per-opponent statistics across sessions using SQLite:
    * VPIP (Voluntarily Put $ In Pot)
    * PFR (Pre-Flop Raise %)
    * Aggression Factor
    * Fold-to-3bet %
    * C-bet frequency
    * Showdown frequency
    * Average bet sizing (as % of pot)
    * Total hands observed

The database persists in ``reports/opponent_db.sqlite`` by default and
survives bot restarts.  Statistics are exposed as
:class:`OpponentProfile` dataclass objects that feed directly into the
GTO mixed-strategy engine.

Classification
--------------
Based on accumulated stats, opponents are classified as:

* **Fish** — VPIP > 55%, Aggression < 1.2 (calls too much, rarely raises).
* **Nit** — VPIP < 22% (plays very few hands, but strong when involved).
* **LAG** — VPIP > 40%, Aggression > 2.5 (loose-aggressive — lots of bluffs).
* **TAG** — VPIP 22–35%, Aggression 1.5–2.5 (solid, balanced).
* **Unknown** — fewer than 15 hands observed.

Environment variables
---------------------
``TITAN_OPPONENT_DB_PATH``  SQLite database path (default: ``reports/opponent_db.sqlite``).
``TITAN_OPPONENT_DB_OFF``   ``1`` to disable persistence (stats are in-memory only).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any


# ── Opponent profile dataclass ──────────────────────────────────────────

@dataclass(slots=True)
class OpponentProfile:
    """Aggregated statistics for a single opponent.

    Attributes:
        player_id:       Unique opponent identifier (name or seat hash).
        vpip:            Voluntarily Put $ In Pot — % of hands where player
                         voluntarily put money in preflop.
        pfr:             Pre-Flop Raise — % of hands where player raised preflop.
        aggression:      Aggression Factor — (bets + raises) / calls.
        fold_to_3bet:    Fold to 3-bet — % of time player folds to a re-raise.
        cbet_freq:       Continuation bet frequency — % of flops where PFR
                         fires continuation bet.
        showdown_freq:   Showdown frequency — % of hands that reach showdown.
        avg_bet_sizing:  Average bet size as fraction of pot (e.g. 0.67 = 2/3 pot).
        hands_observed:  Total hand count for this opponent.
        classification:  Auto-classified player type.
        last_seen:       UTC timestamp of last observation.
    """
    player_id: str = ""
    vpip: float = 0.50
    pfr: float = 0.20
    aggression: float = 1.0
    fold_to_3bet: float = 0.50
    cbet_freq: float = 0.60
    showdown_freq: float = 0.50
    avg_bet_sizing: float = 0.67
    hands_observed: int = 0
    classification: str = "Unknown"
    last_seen: float = 0.0


# ── Hand event for recording ───────────────────────────────────────────

@dataclass(slots=True)
class HandEvent:
    """A single event in a hand to update opponent statistics.

    Attributes:
        player_id:       Opponent identifier.
        street:          preflop / flop / turn / river.
        action:          fold / call / raise / check / bet / all_in.
        is_voluntary:    True if the player voluntarily put money in (not BB check).
        is_preflop_raise: True if this was a preflop raise (counts toward PFR).
        is_3bet_spot:    True if this was a 3-bet opportunity.
        folded_to_3bet:  True if the player folded to a 3-bet.
        is_cbet_spot:    True if this was a c-bet opportunity (PFR on flop).
        did_cbet:        True if the player fired a c-bet.
        reached_showdown: True if the hand went to showdown.
        bet_size_ratio:  Bet/raise size as fraction of pot (0.0 if fold/call/check).
    """
    player_id: str = ""
    street: str = "preflop"
    action: str = "check"
    is_voluntary: bool = False
    is_preflop_raise: bool = False
    is_3bet_spot: bool = False
    folded_to_3bet: bool = False
    is_cbet_spot: bool = False
    did_cbet: bool = False
    reached_showdown: bool = False
    bet_size_ratio: float = 0.0


# ── Database ────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS opponent_stats (
    player_id        TEXT PRIMARY KEY,
    total_hands      INTEGER DEFAULT 0,
    voluntary_hands  INTEGER DEFAULT 0,
    pfr_hands        INTEGER DEFAULT 0,
    total_bets       INTEGER DEFAULT 0,
    total_raises     INTEGER DEFAULT 0,
    total_calls      INTEGER DEFAULT 0,
    three_bet_spots  INTEGER DEFAULT 0,
    folded_to_3bet   INTEGER DEFAULT 0,
    cbet_spots       INTEGER DEFAULT 0,
    cbet_fired       INTEGER DEFAULT 0,
    showdown_hands   INTEGER DEFAULT 0,
    bet_size_sum     REAL    DEFAULT 0.0,
    bet_size_count   INTEGER DEFAULT 0,
    last_seen        REAL    DEFAULT 0.0
);
"""


def _classify(vpip: float, pfr: float, aggression: float, hands: int) -> str:
    """Classify an opponent based on their stats."""
    if hands < 15:
        return "Unknown"
    if vpip > 0.55 and aggression < 1.2:
        return "Fish"
    if vpip < 0.22:
        return "Nit"
    if vpip > 0.40 and aggression > 2.5:
        return "LAG"
    if 0.22 <= vpip <= 0.35 and 1.5 <= aggression <= 2.5:
        return "TAG"
    if vpip > 0.40:
        return "Loose-Passive" if aggression < 1.5 else "LAG"
    return "TAG"


class OpponentDB:
    """Persistent opponent profiling database backed by SQLite.

    Thread-safe — uses a lock for all write operations.  Reads are
    lock-free because SQLite handles concurrent reads natively.

    Usage::

        db = OpponentDB()
        db.record_hand_start("player_abc")
        db.record_event(HandEvent(player_id="player_abc", ...))
        profile = db.get_profile("player_abc")
        print(profile.classification)  # "Fish", "Nit", "LAG", "TAG", "Unknown"
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._lock = threading.Lock()

        disabled = os.getenv("TITAN_OPPONENT_DB_OFF", "0").strip().lower()
        self._disabled = disabled in {"1", "true", "yes", "on"}

        if db_path is None:
            db_path = os.getenv(
                "TITAN_OPPONENT_DB_PATH",
                os.path.join("reports", "opponent_db.sqlite"),
            )

        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

        if not self._disabled:
            self._init_db()

    def _init_db(self) -> None:
        """Create the database and table if they don't exist."""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.commit()
        except Exception:
            self._conn = None
            self._disabled = True

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Recording API ───────────────────────────────────────────────

    def record_hand_start(self, player_id: str) -> None:
        """Increment the total hand count for an opponent.

        Call this once per hand, per opponent observed at the table.
        """
        if self._disabled or not self._conn or not player_id:
            return
        with self._lock:
            self._conn.execute(
                """INSERT INTO opponent_stats (player_id, total_hands, last_seen)
                   VALUES (?, 1, ?)
                   ON CONFLICT(player_id)
                   DO UPDATE SET total_hands = total_hands + 1,
                                 last_seen = excluded.last_seen;""",
                (player_id, time.time()),
            )
            self._conn.commit()

    def record_event(self, event: HandEvent) -> None:
        """Record a single action event to update opponent statistics."""
        if self._disabled or not self._conn or not event.player_id:
            return
        with self._lock:
            self._ensure_player(event.player_id)

            updates: list[str] = []
            if event.is_voluntary:
                updates.append("voluntary_hands = voluntary_hands + 1")
            if event.is_preflop_raise:
                updates.append("pfr_hands = pfr_hands + 1")

            action_lower = event.action.strip().lower()
            if action_lower in {"bet", "raise", "raise_small", "raise_big", "all_in"}:
                updates.append("total_bets = total_bets + 1")
                if action_lower.startswith("raise") or action_lower == "all_in":
                    updates.append("total_raises = total_raises + 1")
            elif action_lower == "call":
                updates.append("total_calls = total_calls + 1")

            if event.is_3bet_spot:
                updates.append("three_bet_spots = three_bet_spots + 1")
                if event.folded_to_3bet:
                    updates.append("folded_to_3bet = folded_to_3bet + 1")

            if event.is_cbet_spot:
                updates.append("cbet_spots = cbet_spots + 1")
                if event.did_cbet:
                    updates.append("cbet_fired = cbet_fired + 1")

            if event.reached_showdown:
                updates.append("showdown_hands = showdown_hands + 1")

            if event.bet_size_ratio > 0:
                updates.append(f"bet_size_sum = bet_size_sum + {event.bet_size_ratio}")
                updates.append("bet_size_count = bet_size_count + 1")

            updates.append(f"last_seen = {time.time()}")

            if updates:
                sql = f"UPDATE opponent_stats SET {', '.join(updates)} WHERE player_id = ?;"
                self._conn.execute(sql, (event.player_id,))
                self._conn.commit()

    def record_batch(self, events: list[HandEvent]) -> None:
        """Record multiple events in a single transaction."""
        if self._disabled or not self._conn:
            return
        for event in events:
            self.record_event(event)

    # ── Query API ───────────────────────────────────────────────────

    def get_profile(self, player_id: str) -> OpponentProfile:
        """Retrieve the aggregated profile for an opponent.

        Returns a default (Unknown) profile if the player is not in the DB.
        """
        default = OpponentProfile(player_id=player_id)
        if self._disabled or not self._conn or not player_id:
            return default

        with self._lock:
            row = self._conn.execute(
                """SELECT total_hands, voluntary_hands, pfr_hands,
                          total_bets, total_raises, total_calls,
                          three_bet_spots, folded_to_3bet,
                          cbet_spots, cbet_fired,
                          showdown_hands, bet_size_sum, bet_size_count,
                          last_seen
                   FROM opponent_stats WHERE player_id = ?;""",
                (player_id,),
            ).fetchone()

        if row is None:
            return default

        (
            total_hands, voluntary_hands, pfr_hands,
            total_bets, total_raises, total_calls,
            three_bet_spots, folded_to_3bet,
            cbet_spots, cbet_fired,
            showdown_hands, bet_size_sum, bet_size_count,
            last_seen,
        ) = row

        # Compute derived stats (safe division)
        vpip = voluntary_hands / max(total_hands, 1)
        pfr = pfr_hands / max(total_hands, 1)
        aggression = (total_bets + total_raises) / max(total_calls, 1)
        fold_3bet = folded_to_3bet / max(three_bet_spots, 1)
        cbet = cbet_fired / max(cbet_spots, 1)
        sd_freq = showdown_hands / max(total_hands, 1)
        avg_sizing = bet_size_sum / max(bet_size_count, 1)

        classification = _classify(vpip, pfr, aggression, total_hands)

        return OpponentProfile(
            player_id=player_id,
            vpip=round(vpip, 4),
            pfr=round(pfr, 4),
            aggression=round(aggression, 4),
            fold_to_3bet=round(fold_3bet, 4),
            cbet_freq=round(cbet, 4),
            showdown_freq=round(sd_freq, 4),
            avg_bet_sizing=round(avg_sizing, 4),
            hands_observed=total_hands,
            classification=classification,
            last_seen=last_seen,
        )

    def get_all_profiles(self) -> list[OpponentProfile]:
        """Retrieve profiles for all known opponents."""
        if self._disabled or not self._conn:
            return []

        with self._lock:
            rows = self._conn.execute(
                "SELECT player_id FROM opponent_stats ORDER BY last_seen DESC;"
            ).fetchall()
        return [self.get_profile(row[0]) for row in rows]

    def get_table_summary(self, player_ids: list[str]) -> dict[str, OpponentProfile]:
        """Retrieve profiles for a specific set of players (current table)."""
        return {pid: self.get_profile(pid) for pid in player_ids if pid}

    def get_classification(self, player_id: str) -> str:
        """Quick classification without full profile computation."""
        profile = self.get_profile(player_id)
        return profile.classification

    # ── Conversion to GTO engine format ─────────────────────────────

    def to_gto_tendencies(self, player_id: str, min_hands: int = 50) -> Any:
        """Convert an opponent profile to an OpponentTendencies for the GTO engine.

        Returns ``None`` if fewer than *min_hands* observed — until the
        sample is large enough, the bot plays pure GTO (equilibrado)
        rather than attempting exploitative adjustments that might be
        based on noise.

        Default is 50 hands (changed from 15) per Red Team review.
        """
        from workflows.gto_engine import OpponentTendencies

        profile = self.get_profile(player_id)
        if profile.hands_observed < min_hands:
            return None

        return OpponentTendencies(
            vpip=profile.vpip,
            pfr=profile.pfr,
            aggression=profile.aggression,
            fold_to_3bet=profile.fold_to_3bet,
            cbet_freq=profile.cbet_freq,
            hands_observed=profile.hands_observed,
        )

    # ── Internal helpers ────────────────────────────────────────────

    def _ensure_player(self, player_id: str) -> None:
        """Insert a player row if it doesn't exist (no-op if it does)."""
        if self._conn is None:
            return
        self._conn.execute(
            """INSERT OR IGNORE INTO opponent_stats (player_id, last_seen)
               VALUES (?, ?);""",
            (player_id, time.time()),
        )

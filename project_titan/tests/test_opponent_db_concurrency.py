"""Concurrency stress test for OpponentDB — proves the SQLite
database survives 4 simultaneous LDPlayer bot instances hammering
it with reads and writes.

Usage::

    cd project_titan
    python -m pytest tests/test_opponent_db_concurrency.py -v

Each test simulates the **production topology**: 4 threads, each with
its **own** ``OpponentDB`` instance (= own SQLite connection) pointing
at the **same** database file.  This mirrors real-world multi-process
LDPlayer usage where every bot process opens its own connection.

Assertions:

1. No SQLite "database is locked" errors.
2. All hand counts match expected totals (no lost writes).
3. Total wall-clock time stays under a generous bound (no deadlocks).
4. Profiles are consistent (VPIP in [0,1], etc.).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from memory.opponent_db import OpponentDB, HandEvent, OpponentProfile


# Number of simulated LDPlayer instances
NUM_INSTANCES = 4
# Hands per instance per opponent
HANDS_PER_INSTANCE = 200
# Unique opponents each instance tracks
OPPONENTS_PER_INSTANCE = 6


def _bot_worker(
    db_path: str,
    instance_id: int,
    errors: list[str],
    timings: list[float],
) -> None:
    """Simulate one LDPlayer bot instance performing DB operations.

    Each instance creates its **own** ``OpponentDB`` (own connection),
    mirroring the real multi-process topology.
    """
    db: OpponentDB | None = None
    t0 = time.perf_counter()
    try:
        db = OpponentDB(db_path=db_path)

        for hand_num in range(HANDS_PER_INSTANCE):
            for opp_idx in range(OPPONENTS_PER_INSTANCE):
                # Opponent ID is shared across instances (same opponents at table)
                pid = f"opponent_{opp_idx}"

                # Write: record hand start
                db.record_hand_start(pid)

                # Write: record an action event
                is_vol = (hand_num + opp_idx) % 3 != 0  # ~67% voluntary
                action = ["call", "raise", "fold", "check"][hand_num % 4]
                db.record_event(HandEvent(
                    player_id=pid,
                    is_voluntary=is_vol,
                    is_preflop_raise=(action == "raise"),
                    action=action,
                    bet_size_ratio=0.67 if action in ("raise", "call") else 0.0,
                ))

                # Read: profile lookup (every 10th hand)
                if hand_num % 10 == 0:
                    profile = db.get_profile(pid)
                    if profile.hands_observed < 0:
                        errors.append(
                            f"Instance {instance_id}: negative hands for {pid}"
                        )
                    if not (0.0 <= profile.vpip <= 1.0):
                        errors.append(
                            f"Instance {instance_id}: VPIP={profile.vpip} out of range for {pid}"
                        )

    except Exception as exc:
        errors.append(f"Instance {instance_id}: {type(exc).__name__}: {exc}")
    finally:
        if db is not None:
            db.close()
        timings.append(time.perf_counter() - t0)


class TestOpponentDBConcurrency:
    """Stress test: 4 concurrent writers using separate connections to
    the same SQLite database (mirrors multi-process LDPlayer usage)."""

    def _make_shared_path(self) -> str:
        """Create a temp file and return its path (caller deletes it)."""
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        # Initialize the schema once
        init_db = OpponentDB(db_path=tmp.name)
        init_db.close()
        return tmp.name

    def test_4_concurrent_instances_no_errors(self):
        """4 threads × 200 hands × 6 opponents = 4800 write bursts, zero errors."""
        db_path = self._make_shared_path()
        try:
            errors: list[str] = []
            timings: list[float] = []
            threads: list[threading.Thread] = []

            for i in range(NUM_INSTANCES):
                t = threading.Thread(
                    target=_bot_worker,
                    args=(db_path, i, errors, timings),
                    daemon=True,
                )
                threads.append(t)

            wall_start = time.perf_counter()
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            wall_elapsed = time.perf_counter() - wall_start

            # Assert: no errors from any thread
            assert errors == [], f"Errors detected: {errors}"

            # Assert: all threads finished
            assert len(timings) == NUM_INSTANCES, (
                f"Only {len(timings)}/{NUM_INSTANCES} threads completed"
            )

            # Assert: total time under 15 seconds (generous bound — no deadlocks)
            assert wall_elapsed < 15.0, (
                f"Wall clock {wall_elapsed:.2f}s exceeds 15s — possible deadlock"
            )

            print(f"\n  Wall clock: {wall_elapsed:.2f}s")
            for i, t_elapsed in enumerate(timings):
                print(f"  Instance {i}: {t_elapsed:.2f}s")

        finally:
            os.unlink(db_path)

    def test_hand_counts_are_consistent(self):
        """Total hand count per opponent = NUM_INSTANCES × HANDS_PER_INSTANCE."""
        db_path = self._make_shared_path()
        try:
            errors: list[str] = []
            timings: list[float] = []
            threads: list[threading.Thread] = []

            for i in range(NUM_INSTANCES):
                t = threading.Thread(
                    target=_bot_worker,
                    args=(db_path, i, errors, timings),
                    daemon=True,
                )
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

            assert errors == [], f"Errors: {errors}"

            # Read back with a fresh connection
            check_db = OpponentDB(db_path=db_path)
            try:
                # Each opponent should have exactly NUM_INSTANCES × HANDS_PER_INSTANCE hands
                expected_total = NUM_INSTANCES * HANDS_PER_INSTANCE
                for opp_idx in range(OPPONENTS_PER_INSTANCE):
                    pid = f"opponent_{opp_idx}"
                    profile = check_db.get_profile(pid)
                    assert profile.hands_observed == expected_total, (
                        f"{pid}: expected {expected_total} hands, got {profile.hands_observed}"
                    )
                    # Classification should not be Unknown with 800 hands
                    assert profile.classification != "Unknown", (
                        f"{pid}: still Unknown after {profile.hands_observed} hands"
                    )
                    # Stats should be in valid ranges
                    assert 0.0 <= profile.vpip <= 1.0
                    assert 0.0 <= profile.pfr <= 1.0
                    assert profile.aggression >= 0.0
                    assert 0.0 <= profile.showdown_freq <= 1.0

                print(f"\n  All {OPPONENTS_PER_INSTANCE} opponents have {expected_total} hands each")
            finally:
                check_db.close()
        finally:
            os.unlink(db_path)

    def test_concurrent_reads_under_write_pressure(self):
        """Heavy reads while writes are ongoing — no stale reads or crashes."""
        db_path = self._make_shared_path()
        try:
            stop_event = threading.Event()
            read_errors: list[str] = []
            read_count = [0]

            def _reader():
                rdb = OpponentDB(db_path=db_path)
                try:
                    while not stop_event.is_set():
                        try:
                            for i in range(OPPONENTS_PER_INSTANCE):
                                p = rdb.get_profile(f"opponent_{i}")
                                read_count[0] += 1
                                if p.hands_observed < 0:
                                    read_errors.append(f"Negative hands: {p}")
                        except Exception as e:
                            read_errors.append(f"Read error: {e}")
                        time.sleep(0.001)
                finally:
                    rdb.close()

            # Start readers (each with own connection)
            readers = [threading.Thread(target=_reader, daemon=True) for _ in range(3)]
            for r in readers:
                r.start()

            # Run writers (each with own connection via _bot_worker)
            errors: list[str] = []
            timings: list[float] = []
            writers = []
            for i in range(NUM_INSTANCES):
                t = threading.Thread(
                    target=_bot_worker,
                    args=(db_path, i, errors, timings),
                    daemon=True,
                )
                writers.append(t)
            for t in writers:
                t.start()
            for t in writers:
                t.join(timeout=60)

            stop_event.set()
            for r in readers:
                r.join(timeout=5)

            assert errors == [], f"Write errors: {errors}"
            assert read_errors == [], f"Read errors: {read_errors}"
            assert read_count[0] > 0, "No reads completed"
            print(f"\n  Completed {read_count[0]} reads under write pressure")

        finally:
            os.unlink(db_path)

    def test_latency_per_operation(self):
        """Single-operation latency should be under 5ms for reads, 10ms for writes."""
        db_path = self._make_shared_path()
        db = OpponentDB(db_path=db_path)
        try:
            # Seed some data
            for i in range(100):
                db.record_hand_start("latency_test")
                db.record_event(HandEvent(
                    player_id="latency_test",
                    is_voluntary=True,
                    action="call",
                ))

            # Measure read latency
            read_times = []
            for _ in range(500):
                t0 = time.perf_counter()
                db.get_profile("latency_test")
                read_times.append((time.perf_counter() - t0) * 1000)

            # Measure write latency
            write_times = []
            for i in range(500):
                t0 = time.perf_counter()
                db.record_hand_start("latency_test")
                write_times.append((time.perf_counter() - t0) * 1000)

            avg_read = sum(read_times) / len(read_times)
            avg_write = sum(write_times) / len(write_times)
            p99_read = sorted(read_times)[int(len(read_times) * 0.99)]
            p99_write = sorted(write_times)[int(len(write_times) * 0.99)]

            print(f"\n  Read:  avg={avg_read:.2f}ms  p99={p99_read:.2f}ms")
            print(f"  Write: avg={avg_write:.2f}ms  p99={p99_write:.2f}ms")

            assert avg_read < 5.0, f"Avg read {avg_read:.2f}ms exceeds 5ms"
            assert avg_write < 10.0, f"Avg write {avg_write:.2f}ms exceeds 10ms"
            # P99 should stay under 50ms even worst case
            assert p99_read < 50.0, f"P99 read {p99_read:.2f}ms exceeds 50ms"
            assert p99_write < 50.0, f"P99 write {p99_write:.2f}ms exceeds 50ms"

        finally:
            db.close()
            os.unlink(db_path)

"""Tests for memory.redis_memory — dual-backend key-value store.

Runs against the in-memory fallback backend (no Redis required).
"""

from __future__ import annotations

import time

from memory.redis_memory import RedisMemory


def _make_memory() -> RedisMemory:
    """Create a RedisMemory that always uses in-memory backend."""
    mem = RedisMemory.__new__(RedisMemory)
    mem.redis_url = ""
    mem.ttl_seconds = 5
    mem._cache = {}
    mem._expires_at = {}
    mem._redis_client = None
    mem.backend = "memory"
    return mem


class TestRedisMemoryInMemory:
    def test_set_and_get(self) -> None:
        mem = _make_memory()
        mem.set("key1", {"a": 1})
        assert mem.get("key1") == {"a": 1}

    def test_get_default(self) -> None:
        mem = _make_memory()
        assert mem.get("missing") is None
        assert mem.get("missing", 42) == 42

    def test_ttl_expiry(self) -> None:
        mem = _make_memory()
        mem.set("short", "value", ttl=1)
        assert mem.get("short") == "value"
        # Manually expire
        mem._expires_at["short"] = time.time() - 1
        assert mem.get("short") is None

    def test_ttl_zero_no_expiry(self) -> None:
        mem = _make_memory()
        mem.set("persist", "forever", ttl=0)
        assert mem.get("persist") == "forever"
        assert "persist" not in mem._expires_at

    def test_delete(self) -> None:
        mem = _make_memory()
        mem.set("del_me", 99)
        assert mem.delete("del_me") is True
        assert mem.get("del_me") is None
        assert mem.delete("del_me") is False

    def test_exists(self) -> None:
        mem = _make_memory()
        mem.set("exists_key", "val")
        assert mem.exists("exists_key") is True
        assert mem.exists("nope") is False

    def test_exists_expired(self) -> None:
        mem = _make_memory()
        mem.set("exp_key", "val", ttl=1)
        mem._expires_at["exp_key"] = time.time() - 1
        assert mem.exists("exp_key") is False

    def test_keys(self) -> None:
        mem = _make_memory()
        mem.set("titan:a", 1, ttl=0)
        mem.set("titan:b", 2, ttl=0)
        mem.set("other", 3, ttl=0)
        result = mem.keys("titan:*")
        assert sorted(result) == ["titan:a", "titan:b"]

    def test_keys_filters_expired(self) -> None:
        mem = _make_memory()
        mem.set("ns:alive", 1, ttl=0)
        mem.set("ns:dead", 2, ttl=1)
        mem._expires_at["ns:dead"] = time.time() - 1
        result = mem.keys("ns:*")
        assert result == ["ns:alive"]

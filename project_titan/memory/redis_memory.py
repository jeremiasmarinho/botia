"""Redis-backed (or in-memory fallback) key-value store.

Attempts to connect to Redis on init.  If the connection fails, falls
back to a local dict with TTL-based expiry — the rest of the system
works identically regardless of the backend.

The active backend (``"redis"`` or ``"memory"``) is exposed via
:attr:`RedisMemory.backend` for logging / diagnostics.
"""

from __future__ import annotations

import json
import importlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("titan.memory.redis")


@dataclass(slots=True)
class RedisMemory:
    """Dual-backend key-value store (Redis / in-memory).

    Attributes:
        redis_url:     Redis connection URL.
        ttl_seconds:   Default time-to-live for stored values.
        backend:       ``"redis"`` or ``"memory"`` (set during init).
    """

    redis_url: str = "redis://:titan_secret@127.0.0.1:6379/0"
    ttl_seconds: int = 5
    _cache: dict[str, Any] = field(default_factory=dict)
    _expires_at: dict[str, float] = field(default_factory=dict)
    _redis_client: Any = field(init=False, default=None)
    backend: str = field(init=False, default="memory")

    def __post_init__(self) -> None:
        try:
            redis_module = importlib.import_module("redis")
            client = redis_module.Redis.from_url(self.redis_url, decode_responses=True)
            client.ping()
            self._redis_client = client
            self.backend = "redis"
        except Exception as exc:
            self._redis_client = None
            self.backend = "memory"
            _log.warning("Redis unavailable (%s), using in-memory fallback", exc)

    def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """Store *value* under *key*.

        *ttl* overrides the instance default.  Use ``ttl=0`` to persist
        without expiry (Redis: no TTL command; memory: no expiry).
        """
        effective_ttl = ttl if ttl is not None else self.ttl_seconds

        if self._redis_client is not None:
            payload = json.dumps(value)
            if effective_ttl > 0:
                self._redis_client.setex(key, effective_ttl, payload)
            else:
                self._redis_client.set(key, payload)
            return

        self._cache[key] = value
        if effective_ttl > 0:
            self._expires_at[key] = time.time() + effective_ttl
        else:
            self._expires_at.pop(key, None)  # no expiry

    def get(self, key: str, default: Any = None) -> Any:
        if self._redis_client is not None:
            payload = self._redis_client.get(key)
            if payload is None:
                return default
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return default

        expires_at = self._expires_at.get(key)
        if expires_at is not None and expires_at <= time.time():
            self._cache.pop(key, None)
            self._expires_at.pop(key, None)
            return default

        return self._cache.get(key, default)

    def delete(self, key: str) -> bool:
        """Remove *key*. Returns ``True`` if the key existed."""
        if self._redis_client is not None:
            return bool(self._redis_client.delete(key))
        existed = key in self._cache
        self._cache.pop(key, None)
        self._expires_at.pop(key, None)
        return existed

    def exists(self, key: str) -> bool:
        """Check whether *key* is present (and not expired)."""
        if self._redis_client is not None:
            return bool(self._redis_client.exists(key))
        expires_at = self._expires_at.get(key)
        if expires_at is not None and expires_at <= time.time():
            self._cache.pop(key, None)
            self._expires_at.pop(key, None)
            return False
        return key in self._cache

    def keys(self, pattern: str = "*") -> list[str]:
        """Return keys matching *pattern* (glob-style for Redis, prefix for memory)."""
        if self._redis_client is not None:
            return [k for k in self._redis_client.keys(pattern)]
        # Simple prefix matching for in-memory backend
        now = time.time()
        prefix = pattern.rstrip("*")
        result: list[str] = []
        for key in list(self._cache):
            exp = self._expires_at.get(key)
            if exp is not None and exp <= now:
                self._cache.pop(key, None)
                self._expires_at.pop(key, None)
                continue
            if key.startswith(prefix):
                result.append(key)
        return result

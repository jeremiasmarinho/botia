from __future__ import annotations

import json
import importlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RedisMemory:
    redis_url: str = "redis://127.0.0.1:6379/0"
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
        except Exception:
            self._redis_client = None
            self.backend = "memory"

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

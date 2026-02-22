"""Shared protocol for memory-backed components.

Defines the minimal interface that any memory backend (Redis, in-memory dict,
etc.) must satisfy to be used by the workflow and agent layers.

This lives in its own module so that type-checking imports do not pull in
heavyweight dependencies (Redis, ZMQ, etc.).
"""

from __future__ import annotations

from typing import Any, Protocol


class SupportsMemory(Protocol):
    """Structural subtype for a key-value store used across the pipeline.

    Implementations:
    * :class:`memory.redis_memory.RedisMemory` (production)
    * ``dict``-wrapper in E2E tests
    """

    def set(self, key: str, value: Any, *, ttl: int = 0) -> None:
        """Persist *value* under *key* with optional TTL in seconds."""
        ...

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve the value stored at *key*, or *default* if missing."""
        ...

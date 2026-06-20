"""TTL-based cache for analysis endpoints.

When REDIS_URL is set the cache is backed by Redis and shared across all
worker processes.  Otherwise falls back to a single-process in-memory
TTLCache (suitable for development or single-worker deployments).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Any

from cachetools import TTLCache

from app.config import settings

_TTL = settings.cache_ttl


class _InMemoryCache:
    """Async-safe wrapper around cachetools.TTLCache (single-process)."""

    def __init__(self, ttl: int = 300, maxsize: int = 128) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = asyncio.Lock()

    def _make_key(self, endpoint: str, **params: Any) -> str:
        raw = json.dumps(
            {"endpoint": endpoint, "params": params}, sort_keys=True, default=str
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            return self._cache.get(key)

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._cache[key] = value

    async def invalidate(self) -> None:
        async with self._lock:
            self._cache.clear()


class _RedisCache:
    """Redis-backed cache shared across all worker processes.

    Requires the ``redis`` package (``pip install redis``).
    Configure via the ``REDIS_URL`` environment variable,
    e.g. ``redis://localhost:6379/0``.
    """

    _PREFIX = "rpa:"

    def __init__(self, url: str, ttl: int = 300) -> None:
        import redis.asyncio as aioredis
        self._client = aioredis.from_url(url, decode_responses=False)
        self._ttl = ttl

    def _make_key(self, endpoint: str, **params: Any) -> str:
        raw = json.dumps(
            {"endpoint": endpoint, "params": params}, sort_keys=True, default=str
        )
        return self._PREFIX + hashlib.sha256(raw.encode()).hexdigest()

    async def get(self, key: str) -> Any | None:
        data = await self._client.get(key)
        if data is None:
            return None
        return json.loads(data)

    async def set(self, key: str, value: Any) -> None:
        await self._client.set(key, json.dumps(value, default=str), ex=self._ttl)

    async def invalidate(self) -> None:
        cursor = 0
        while True:
            cursor, keys = await self._client.scan(
                cursor, match=f"{self._PREFIX}*", count=100
            )
            if keys:
                await self._client.delete(*keys)
            if cursor == 0:
                break


_REDIS_URL = os.getenv("REDIS_URL")

if _REDIS_URL:
    analysis_cache: _InMemoryCache | _RedisCache = _RedisCache(_REDIS_URL, ttl=_TTL)
else:
    analysis_cache = _InMemoryCache(ttl=_TTL)

# Public alias kept for tests and any external callers that reference AnalysisCache directly.
AnalysisCache = _InMemoryCache
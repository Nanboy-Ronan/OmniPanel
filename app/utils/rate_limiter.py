"""Redis-backed login failure rate limiter.

Keyed by (identifier, endpoint) where ``identifier`` is the email address for
the password-login endpoint and the client IP for other endpoints (e.g. WeCom
exchange). Keying by email rather than IP prevents one user's failed attempts
from locking everyone else out when all Streamlit→FastAPI traffic shares the
same loopback IP (127.0.0.1).

When Redis is unavailable, falls back to a process-local in-memory counter
instead of letting every request through unchecked. The fallback is weaker
under multiple worker processes (each worker counts independently), but it
closes the "Redis outage means no login protection at all" gap.

Configuration (via environment variables):
  LOGIN_MAX_ATTEMPTS    – failures allowed per window (default 5)
  LOGIN_LOCKOUT_SECONDS – rolling window / lockout duration in seconds (default 60)
  REDIS_URL             – Redis connection URL (default redis://localhost:6379/0)
"""
from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS: int = settings.login_max_attempts
WINDOW_SECONDS: int = settings.login_lockout_seconds


class _MemoryFallback:
    """Process-local (ip, endpoint) failure counter used only when Redis is
    unreachable. Each entry's window resets on every failure, matching the
    Redis path's ``INCR`` + ``EXPIRE`` behaviour.
    """

    def __init__(self) -> None:
        self._counts: dict[str, tuple[int, float]] = {}  # key -> (count, expires_at)
        self._lock = Lock()

    def get(self, key: str) -> tuple[int, int]:
        """Return ``(count, seconds_remaining_in_window)``."""
        with self._lock:
            count, expires_at = self._counts.get(key, (0, 0.0))
            if not expires_at:
                return 0, 0
            remaining = expires_at - time.monotonic()
            if remaining <= 0:
                self._counts.pop(key, None)
                return 0, 0
            return count, int(remaining)

    def incr(self, key: str, window_seconds: int) -> None:
        with self._lock:
            count, expires_at = self._counts.get(key, (0, 0.0))
            if expires_at and time.monotonic() >= expires_at:
                count = 0
            self._counts[key] = (count + 1, time.monotonic() + window_seconds)

    def reset(self, key: str) -> None:
        with self._lock:
            self._counts.pop(key, None)


def get_client_ip(request: Request) -> str:
    """Return the connecting client's IP address.

    Trust resolution for X-Forwarded-For happens exactly once, in
    ProxyHeadersMiddleware (added unconditionally in app/main.py, governed by
    FORWARDED_ALLOW_IPS) — by the time a request reaches here,
    ``request.client.host`` is already the real client IP if the connection
    came through a trusted proxy, or the raw socket peer otherwise.

    Do not re-parse X-Forwarded-For here: that would re-trust the header from
    *any* peer regardless of FORWARDED_ALLOW_IPS, which is exactly the spoofing
    hole this function used to have (an attacker could send a fresh fake
    X-Forwarded-For on every request and dodge the per-IP lockout entirely).
    """
    return request.client.host if request.client else "unknown"


class LoginRateLimiter:
    """Track failed login attempts per (identifier, endpoint) pair in Redis.

    Usage::

        limiter = LoginRateLimiter()

        # At the start of a login handler:
        await limiter.check(identifier, "password_login")   # raises 429 if locked

        # After a failed attempt:
        await limiter.record_failure(identifier, "password_login")

        # After a successful login:
        await limiter.reset(identifier, "password_login")
    """

    def __init__(self, *, redis_client: Any = None) -> None:
        # Accept an injected client (used in tests with fakeredis).
        self._client: Any = redis_client
        self._memory = _MemoryFallback()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _redis(self) -> Any | None:
        """Return an active Redis client, or None if unavailable (caller falls
        back to the in-memory counter)."""
        if self._client is not None:
            return self._client
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            import redis.asyncio as aioredis
            c = aioredis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
            )
            await c.ping()
            self._client = c
            return c
        except Exception as exc:
            logger.warning(
                "LoginRateLimiter: Redis unavailable (%s) — using in-memory fallback", exc
            )
            return None

    @staticmethod
    def _key(identifier: str, endpoint: str) -> str:
        return f"rap:login_fail:{endpoint}:{identifier}"

    @staticmethod
    def _raise_429(wait: int) -> None:
        wait = max(wait, 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def check(self, identifier: str, endpoint: str) -> None:
        """Raise HTTP 429 if this identifier has exceeded the failure limit."""
        key = self._key(identifier, endpoint)
        r = await self._redis()
        if r is None:
            count, remaining = self._memory.get(key)
            if count >= MAX_ATTEMPTS:
                self._raise_429(remaining)
            return
        try:
            raw = await r.get(key)
            if raw is not None and int(raw) >= MAX_ATTEMPTS:
                ttl = await r.ttl(key)
                self._raise_429(int(ttl))
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(
                "LoginRateLimiter.check error: %s — using in-memory fallback", exc
            )
            count, remaining = self._memory.get(key)
            if count >= MAX_ATTEMPTS:
                self._raise_429(remaining)

    async def record_failure(self, identifier: str, endpoint: str) -> None:
        """Increment the failure counter; set TTL on first increment."""
        key = self._key(identifier, endpoint)
        r = await self._redis()
        if r is None:
            self._memory.incr(key, WINDOW_SECONDS)
            return
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "LoginRateLimiter.record_failure error: %s — using in-memory fallback", exc
            )
            self._memory.incr(key, WINDOW_SECONDS)

    async def reset(self, identifier: str, endpoint: str) -> None:
        """Clear the failure counter after a successful login."""
        key = self._key(identifier, endpoint)
        r = await self._redis()
        if r is None:
            self._memory.reset(key)
            return
        try:
            await r.delete(key)
        except Exception as exc:
            logger.warning(
                "LoginRateLimiter.reset error: %s — clearing in-memory fallback too", exc
            )
        # Always clear the memory counter too: if Redis was flaky earlier and
        # the fallback path incremented it, a successful login should reset
        # both, not leave a stale fallback count behind.
        self._memory.reset(key)


# Module-level singleton — middleware and endpoints share this instance.
login_rate_limiter = LoginRateLimiter()

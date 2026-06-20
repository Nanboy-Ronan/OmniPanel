"""Redis-backed per-IP login failure rate limiter.

Fails open when Redis is unavailable: requests pass through with a warning
log rather than blocking all logins if the cache layer is down.

Configuration (via environment variables):
  LOGIN_MAX_ATTEMPTS    – failures allowed per window (default 5)
  LOGIN_LOCKOUT_SECONDS – rolling window / lockout duration in seconds (default 60)
  REDIS_URL             – Redis connection URL (default redis://localhost:6379/0)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS: int = settings.login_max_attempts
WINDOW_SECONDS: int = settings.login_lockout_seconds


def get_client_ip(request: Request) -> str:
    """Return the real client IP, honouring X-Forwarded-For (set by nginx)."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class LoginRateLimiter:
    """Track failed login attempts per (IP, endpoint) pair in Redis.

    Usage::

        limiter = LoginRateLimiter()

        # At the start of a login handler:
        await limiter.check(ip, "password_login")   # raises 429 if locked

        # After a failed attempt:
        await limiter.record_failure(ip, "password_login")

        # After a successful login:
        await limiter.reset(ip, "password_login")
    """

    def __init__(self, *, redis_client: Any = None) -> None:
        # Accept an injected client (used in tests with fakeredis).
        self._client: Any = redis_client

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _redis(self) -> Any | None:
        """Return an active Redis client, or None if unavailable (fail-open)."""
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
                "LoginRateLimiter: Redis unavailable (%s) — failing open", exc
            )
            return None

    @staticmethod
    def _key(ip: str, endpoint: str) -> str:
        return f"rap:login_fail:{endpoint}:{ip}"

    # ── Public API ────────────────────────────────────────────────────────────

    async def check(self, ip: str, endpoint: str) -> None:
        """Raise HTTP 429 if this IP has exceeded the failure limit."""
        r = await self._redis()
        if r is None:
            return
        try:
            raw = await r.get(self._key(ip, endpoint))
            if raw is not None and int(raw) >= MAX_ATTEMPTS:
                ttl = await r.ttl(self._key(ip, endpoint))
                wait = max(int(ttl), 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Too many failed login attempts. "
                        f"Try again in {wait} seconds."
                    ),
                    headers={"Retry-After": str(wait)},
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("LoginRateLimiter.check error: %s — failing open", exc)

    async def record_failure(self, ip: str, endpoint: str) -> None:
        """Increment the failure counter; set TTL on first increment."""
        r = await self._redis()
        if r is None:
            return
        try:
            key = self._key(ip, endpoint)
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS)
            await pipe.execute()
        except Exception as exc:
            logger.warning("LoginRateLimiter.record_failure error: %s", exc)

    async def reset(self, ip: str, endpoint: str) -> None:
        """Clear the failure counter after a successful login."""
        r = await self._redis()
        if r is None:
            return
        try:
            await r.delete(self._key(ip, endpoint))
        except Exception as exc:
            logger.warning("LoginRateLimiter.reset error: %s", exc)


# Module-level singleton — middleware and endpoints share this instance.
login_rate_limiter = LoginRateLimiter()

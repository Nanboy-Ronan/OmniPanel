"""Tests for login rate limiting.

TDD: these tests are written before the implementation.

Coverage:
  Unit (no DB/HTTP, direct class tests with injected fake Redis):
    - check() passes when under the failure limit
    - check() raises 429 after MAX_ATTEMPTS failures
    - record_failure() increments the counter
    - reset() clears the counter so check() passes again
    - Different IPs have independent counters
    - Different endpoints have independent counters
    - No Redis configured → fail-open (no exception raised)

  Integration (TestClient + real test DB + injected fake Redis):
    - POST /auth/jwt/login: 5 wrong passwords → 6th attempt returns 429
    - POST /auth/jwt/login: successful login after < MAX failures resets counter
    - POST /auth/wecom/exchange: rate limited per IP
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _run(coro):
    """Run a coroutine in a fresh event loop (for sync test bodies)."""
    return asyncio.run(coro)


def _fake_redis():
    """Return a FakeAsyncRedis instance (in-memory, no server needed)."""
    import fakeredis
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def _make_limiter(redis_client=None):
    from app.utils.rate_limiter import LoginRateLimiter
    return LoginRateLimiter(redis_client=redis_client or _fake_redis())


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════
# Section 1: Unit tests — LoginRateLimiter class
# ═══════════════════════════════════════════════════════════════

class TestLoginRateLimiterUnit:
    """Direct tests of the LoginRateLimiter class with injected fake Redis."""

    def test_check_passes_when_under_limit(self):
        limiter = _make_limiter()

        async def _test():
            for _ in range(4):
                await limiter.record_failure("1.2.3.4", "test_ep")
            await limiter.check("1.2.3.4", "test_ep")  # must not raise

        _run(_test())

    def test_check_blocks_after_max_attempts(self):
        from app.utils.rate_limiter import MAX_ATTEMPTS
        from fastapi import HTTPException

        limiter = _make_limiter()

        async def _test():
            for _ in range(MAX_ATTEMPTS):
                await limiter.record_failure("1.2.3.4", "test_ep")
            with pytest.raises(HTTPException) as exc_info:
                await limiter.check("1.2.3.4", "test_ep")
            assert exc_info.value.status_code == 429

        _run(_test())

    def test_response_429_has_retry_after_header(self):
        from app.utils.rate_limiter import MAX_ATTEMPTS
        from fastapi import HTTPException

        limiter = _make_limiter()

        async def _test():
            for _ in range(MAX_ATTEMPTS):
                await limiter.record_failure("1.2.3.4", "test_ep")
            with pytest.raises(HTTPException) as exc_info:
                await limiter.check("1.2.3.4", "test_ep")
            assert "Retry-After" in (exc_info.value.headers or {})

        _run(_test())

    def test_reset_clears_counter_so_check_passes(self):
        from app.utils.rate_limiter import MAX_ATTEMPTS

        limiter = _make_limiter()

        async def _test():
            for _ in range(MAX_ATTEMPTS):
                await limiter.record_failure("1.2.3.4", "test_ep")
            await limiter.reset("1.2.3.4", "test_ep")
            await limiter.check("1.2.3.4", "test_ep")  # must not raise

        _run(_test())

    def test_different_ips_are_independent(self):
        from app.utils.rate_limiter import MAX_ATTEMPTS
        from fastapi import HTTPException

        limiter = _make_limiter()

        async def _test():
            for _ in range(MAX_ATTEMPTS):
                await limiter.record_failure("10.0.0.1", "test_ep")
            # 10.0.0.1 is locked, but 10.0.0.2 is not
            await limiter.check("10.0.0.2", "test_ep")  # must not raise
            with pytest.raises(HTTPException):
                await limiter.check("10.0.0.1", "test_ep")

        _run(_test())

    def test_different_endpoints_are_independent(self):
        from app.utils.rate_limiter import MAX_ATTEMPTS
        from fastapi import HTTPException

        limiter = _make_limiter()

        async def _test():
            for _ in range(MAX_ATTEMPTS):
                await limiter.record_failure("1.2.3.4", "endpoint_a")
            # endpoint_a is locked, endpoint_b is not
            await limiter.check("1.2.3.4", "endpoint_b")  # must not raise
            with pytest.raises(HTTPException):
                await limiter.check("1.2.3.4", "endpoint_a")

        _run(_test())

    def test_fails_open_when_redis_unavailable(self):
        """No Redis → no exception raised (availability over strict security)."""
        from app.utils.rate_limiter import LoginRateLimiter, MAX_ATTEMPTS

        limiter = LoginRateLimiter(redis_client=None)
        # Override URL to something unreachable
        original = os.environ.get("REDIS_URL")
        os.environ["REDIS_URL"] = "redis://127.0.0.1:19999/0"

        async def _test():
            for _ in range(MAX_ATTEMPTS + 1):
                await limiter.record_failure("1.2.3.4", "test_ep")
            await limiter.check("1.2.3.4", "test_ep")  # must not raise

        try:
            _run(_test())
        finally:
            if original is None:
                os.environ.pop("REDIS_URL", None)
            else:
                os.environ["REDIS_URL"] = original


# ═══════════════════════════════════════════════════════════════
# Section 2: Integration tests — password login endpoint
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def rl_client(pg_async_url, monkeypatch):
    """TestClient with a fake-Redis rate limiter injected."""
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    import app.db as db

    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SL = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", SL, raising=False)

    import app.main
    importlib.reload(app.main)

    # Inject fake Redis AFTER reload so the reloaded app sees it
    fake_redis = _fake_redis()
    from app.utils import rate_limiter as rl_mod
    rl_mod.login_rate_limiter._client = fake_redis

    from fastapi.testclient import TestClient
    with TestClient(app.main.app) as c:
        yield c

    asyncio.run(engine.dispose())


@pytest.fixture
def rl_tokens(rl_client):
    """Create admin user and return token."""
    rl_client.post(
        "/auth/register",
        json={"email": "admin@test.com", "password": "correct_pw", "role": "viewer"},
    )
    r = rl_client.post(
        "/auth/jwt/login",
        data={"username": "admin@test.com", "password": "correct_pw"},
    )
    admin_token = r.json()["access_token"]
    return {"admin": admin_token, "email": "admin@test.com"}


class TestPasswordLoginRateLimit:

    def test_single_wrong_password_is_not_blocked(self, rl_client, rl_tokens):
        r = rl_client.post(
            "/auth/jwt/login",
            data={"username": rl_tokens["email"], "password": "wrong"},
        )
        assert r.status_code != 429

    def test_five_failures_then_sixth_is_blocked(self, rl_client, rl_tokens):
        from app.utils.rate_limiter import MAX_ATTEMPTS

        for _ in range(MAX_ATTEMPTS):
            rl_client.post(
                "/auth/jwt/login",
                data={"username": rl_tokens["email"], "password": "wrong"},
            )

        r = rl_client.post(
            "/auth/jwt/login",
            data={"username": rl_tokens["email"], "password": "wrong"},
        )
        assert r.status_code == 429
        assert "retry-after" in {k.lower() for k in r.headers}

    def test_429_detail_mentions_wait_time(self, rl_client, rl_tokens):
        from app.utils.rate_limiter import MAX_ATTEMPTS

        for _ in range(MAX_ATTEMPTS):
            rl_client.post(
                "/auth/jwt/login",
                data={"username": rl_tokens["email"], "password": "wrong"},
            )

        r = rl_client.post(
            "/auth/jwt/login",
            data={"username": rl_tokens["email"], "password": "wrong"},
        )
        assert r.status_code == 429
        assert "second" in r.json()["detail"].lower()

    def test_correct_password_after_4_failures_succeeds(self, rl_client, rl_tokens):
        from app.utils.rate_limiter import MAX_ATTEMPTS

        for _ in range(MAX_ATTEMPTS - 1):
            rl_client.post(
                "/auth/jwt/login",
                data={"username": rl_tokens["email"], "password": "wrong"},
            )

        r = rl_client.post(
            "/auth/jwt/login",
            data={"username": rl_tokens["email"], "password": "correct_pw"},
        )
        assert r.status_code == 200

    def test_successful_login_resets_failure_counter(self, rl_client, rl_tokens):
        """After a successful login, the failure counter is cleared
        so a subsequent failure no longer triggers the lockout."""
        from app.utils.rate_limiter import MAX_ATTEMPTS

        # Accumulate MAX_ATTEMPTS - 1 failures
        for _ in range(MAX_ATTEMPTS - 1):
            rl_client.post(
                "/auth/jwt/login",
                data={"username": rl_tokens["email"], "password": "wrong"},
            )

        # Successful login should reset the counter
        rl_client.post(
            "/auth/jwt/login",
            data={"username": rl_tokens["email"], "password": "correct_pw"},
        )

        # Now MAX_ATTEMPTS - 1 more failures should still be under the limit
        for _ in range(MAX_ATTEMPTS - 1):
            r = rl_client.post(
                "/auth/jwt/login",
                data={"username": rl_tokens["email"], "password": "wrong"},
            )
        assert r.status_code != 429

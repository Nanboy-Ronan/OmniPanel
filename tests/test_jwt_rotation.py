"""Tests for RAP_SECRET rotation support (RotatingJWTStrategy).

A single RAP_SECRET is used for signing AND verifying every JWT, with no way
to swap it without invalidating every signed-in session. RotatingJWTStrategy
fixes that: RAP_SECRET_PREVIOUS holds the value being retired, tokens signed
with either the current or the previous secret verify during the rotation
window, and new tokens are always signed with the current (primary) secret
only — so rotation actually completes once the previous secret is dropped.

get_jwt_strategy() is wired up as a FastAPI dependency (fastapi-users calls it
fresh per request), so monkeypatching app.auth.SECRET / settings.rap_secret_previous
between requests in the same test simulates "before rotation" vs "after
rotation" without needing to reload any modules.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import app.db as db


@pytest.fixture
def client(pg_async_url, monkeypatch):
    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", SessionLocal, raising=False)

    import app.main
    importlib.reload(app.main)

    with TestClient(app.main.app) as c:
        yield c
    import asyncio
    asyncio.run(engine.dispose())


def _register_and_login(client, email: str, password: str = "correct_pw") -> str:
    """Register the first user (becomes admin) and return its access token."""
    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "role": "viewer"},
    )
    assert r.status_code == 201, r.text
    r = client.post("/auth/jwt/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


class TestJWTSecretRotation:

    def test_old_secret_token_still_verifies_during_rotation_window(self, client, monkeypatch):
        token = _register_and_login(client, "rotator1@test.com")

        import app.auth as auth_mod
        from app.config import settings

        old_secret = auth_mod.SECRET
        # Simulate the post-rotation state: a new primary secret, with the
        # secret the token was actually signed with moved to "previous".
        monkeypatch.setattr(auth_mod, "SECRET", "rotated-secret-v2")
        monkeypatch.setattr(settings, "rap_secret_previous", old_secret)

        r = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_old_secret_token_rejected_once_previous_secret_dropped(self, client, monkeypatch):
        token = _register_and_login(client, "rotator2@test.com")

        import app.auth as auth_mod

        # Rotation window over: primary secret changed, nothing in "previous".
        monkeypatch.setattr(auth_mod, "SECRET", "rotated-secret-v2")

        r = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_unknown_secret_token_is_always_rejected(self, client, monkeypatch):
        """A token signed with neither the current nor any previous secret
        (e.g. forged, or signed before a secret was ever configured this way)
        must never verify, regardless of RAP_SECRET_PREVIOUS."""
        import app.auth as auth_mod
        from app.config import settings
        from fastapi_users.jwt import generate_jwt

        forged = generate_jwt(
            {"sub": "00000000-0000-0000-0000-000000000000", "aud": ["fastapi-users:auth"]},
            "totally-unrelated-secret",
            3600,
        )
        monkeypatch.setattr(settings, "rap_secret_previous", "also-not-it")

        r = client.get("/admin/users", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401

    def test_new_tokens_are_signed_with_the_current_secret_only(self, client, monkeypatch):
        """New tokens must not verify against a stale previous secret once
        that secret is no longer configured — otherwise rotation would never
        actually complete."""
        import app.auth as auth_mod
        from app.config import settings

        monkeypatch.setattr(auth_mod, "SECRET", "rotated-secret-v2")
        monkeypatch.setattr(settings, "rap_secret_previous", "some-older-secret")

        new_token = _register_and_login(client, "rotator3@test.com")

        # Move on: the new secret becomes "old", and the previous-secret slot
        # is cleared. A token signed under the prior primary must now be dead.
        monkeypatch.setattr(auth_mod, "SECRET", "rotated-secret-v3")
        monkeypatch.setattr(settings, "rap_secret_previous", "")

        r = client.get("/admin/users", headers={"Authorization": f"Bearer {new_token}"})
        assert r.status_code == 401

"""Unit tests for WeChat Work (WeCom) OAuth helpers.

These tests do not require a database connection and cover:
- State signing / verification
- redirect_uri whitelist logic
- Synthetic email generation
- Default role validation
- authorize_url endpoint (no DB dependency)
- exchange endpoint with mocked DB and identity fetch
"""

from __future__ import annotations

import os
import time
import importlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────────────

def _wecom_module():
    import app.views.wecom_auth as m
    importlib.reload(m)
    return m


# ── State signing ─────────────────────────────────────────────────────────────

def test_state_round_trip():
    m = _wecom_module()
    state = m._state()
    payload = m._decode_state(state)
    assert "ts" in payload
    assert "nonce" in payload


def test_state_is_unique():
    m = _wecom_module()
    assert m._state() != m._state()


def test_state_tamper_rejected():
    m = _wecom_module()
    state = m._state()
    tampered = state[:-4] + "XXXX"
    with pytest.raises(Exception):
        m._decode_state(tampered)


def test_state_expired_rejected(monkeypatch):
    m = _wecom_module()
    old_time = int(time.time()) - 700          # 11+ minutes ago
    state = m._sign_state({"ts": old_time, "nonce": "abc"})
    with pytest.raises(Exception):
        m._decode_state(state)


def test_state_missing_ts_rejected():
    m = _wecom_module()
    state = m._sign_state({"nonce": "abc"})    # no 'ts' field
    with pytest.raises(Exception):
        m._decode_state(state)


# ── redirect_uri whitelist ─────────────────────────────────────────────────────

def test_allowed_origins_default(monkeypatch):
    monkeypatch.delenv("WECOM_CONSOLE_REDIRECT_URI", raising=False)
    monkeypatch.delenv("WECOM_STREAMLIT_REDIRECT_URI", raising=False)
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("STREAMLIT_URL", raising=False)
    m = _wecom_module()
    assert m._allowed_redirect_origins() == ["http://localhost:5173/console"]


def test_allowed_origins_from_env(monkeypatch):
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://data.example.com")
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("STREAMLIT_URL", raising=False)
    m = _wecom_module()
    assert "https://data.example.com" in m._allowed_redirect_origins()


def test_allowed_origins_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://data.example.com/")
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("STREAMLIT_URL", raising=False)
    m = _wecom_module()
    assert "https://data.example.com" in m._allowed_redirect_origins()


# ── Synthetic email ───────────────────────────────────────────────────────────

def test_synthetic_email_normal_userid():
    m = _wecom_module()
    email = m._synthetic_email("zhangsan")
    assert email == "wecom.zhangsan@wecom.local"


def test_synthetic_email_special_chars():
    m = _wecom_module()
    email = m._synthetic_email("zhang san!@#")
    assert "@wecom.local" in email
    assert " " not in email


def test_synthetic_email_empty_userid():
    m = _wecom_module()
    email = m._synthetic_email("")
    assert "@wecom.local" in email
    assert len(email) > 10


def test_synthetic_email_long_userid():
    m = _wecom_module()
    email = m._synthetic_email("a" * 200)
    local = email.split("@")[0].replace("wecom.", "")
    assert len(local) <= 48


# ── Default role ──────────────────────────────────────────────────────────────

def test_default_role_default(monkeypatch):
    monkeypatch.delenv("WECOM_DEFAULT_ROLE", raising=False)
    m = _wecom_module()
    assert m._default_role() == "viewer"


def test_default_role_env(monkeypatch):
    monkeypatch.setenv("WECOM_DEFAULT_ROLE", "analyst")
    m = _wecom_module()
    assert m._default_role() == "analyst"


def test_default_role_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("WECOM_DEFAULT_ROLE", "superadmin")
    m = _wecom_module()
    assert m._default_role() == "viewer"


# ── authorize_url endpoint (no DB) ────────────────────────────────────────────

@pytest.fixture
def wecom_app():
    """Minimal FastAPI app with only the WeCom router mounted."""
    import app.views.wecom_auth as m
    app_ = FastAPI()
    app_.include_router(m.router)
    return app_


@pytest.fixture
def wecom_client(wecom_app):
    with TestClient(wecom_app) as c:
        yield c


def _set_wecom_env(monkeypatch):
    monkeypatch.setenv("WECOM_CORP_ID", "wwtest")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000001")
    monkeypatch.setenv("WECOM_APP_SECRET", "testsecret")
    monkeypatch.delenv("WECOM_STREAMLIT_REDIRECT_URI", raising=False)
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("STREAMLIT_URL", raising=False)


def test_authorize_url_returns_valid_url(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    r = wecom_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:5173/console"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    url = body["authorize_url"]
    assert url.startswith("https://open.work.weixin.qq.com/wwopen/sso/qrConnect?")
    assert "appid=wwtest" in url
    assert "agentid=1000001" in url
    assert "state=" in url


def test_authorize_url_state_is_valid(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    r = wecom_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:5173/console"},
    )
    url = r.json()["authorize_url"]
    state = url.split("state=")[1].split("&")[0]
    m = _wecom_module()
    payload = m._decode_state(state)       # must not raise
    assert "ts" in payload


def test_authorize_url_missing_corp_id(wecom_client, monkeypatch):
    monkeypatch.delenv("WECOM_CORP_ID", raising=False)
    monkeypatch.setenv("WECOM_AGENT_ID", "1000001")
    monkeypatch.setenv("WECOM_APP_SECRET", "s")
    # Ensure the redirect_uri passes the allowlist check so we reach the config check
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "http://localhost:5173/console")
    r = wecom_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:5173/console"},
    )
    assert r.status_code == 503


def test_browser_login_start_sets_cookie_and_redirects(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    response = wecom_client.get(
        "/auth/wecom/start",
        params={"redirect_uri": "http://localhost:5173/console", "flow": "qr",
                "return_query": "page=%E6%95%B0%E6%8D%AE%E6%B5%8F%E8%A7%88"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    assert wecom_client.cookies.get("wecom_oauth_state") == state
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Path=/" in response.headers["set-cookie"]
    import base64
    saved = wecom_client.cookies.get("dashboard_login_return")
    assert base64.urlsafe_b64decode(saved + "=" * (-len(saved) % 4)).decode() == "page=%E6%95%B0%E6%8D%AE%E6%B5%8F%E8%A7%88"


def test_login_status_tracks_configuration(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    assert wecom_client.get("/auth/wecom/status").json() == {"enabled": True}
    monkeypatch.delenv("WECOM_APP_SECRET")
    assert wecom_client.get("/auth/wecom/status").json() == {"enabled": False}


def test_authorize_url_blocked_redirect_uri(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://data.example.com")
    r = wecom_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "https://evil.com/callback"},
    )
    assert r.status_code == 400


def test_authorize_url_allowed_redirect_uri(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://data.example.com")
    r = wecom_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "https://data.example.com"},
    )
    assert r.status_code == 200


def test_authorize_url_expires_in_is_int(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    r = wecom_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:5173/console"},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["expires_in"], int)


def test_authorize_url_no_fragment(wecom_client, monkeypatch):
    _set_wecom_env(monkeypatch)
    r = wecom_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:5173/console"},
    )
    assert "#wechat_redirect" not in r.json()["authorize_url"]


# ── exchange endpoint (mocked DB + identity) ──────────────────────────────────

@pytest.fixture
def exchange_client(monkeypatch):
    """TestClient with mocked DB session, identity fetch, and log_operation."""
    _set_wecom_env(monkeypatch)

    import app.views.wecom_auth as wecom_mod
    from app.db import get_session

    fake_user = MagicMock()
    fake_user.id = "11111111-1111-1111-1111-111111111111"
    fake_user.email = "wecom.lisi@wecom.local"
    fake_user.role = "viewer"
    fake_user.is_active = True

    async def fake_get_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = fake_user
        session.execute = AsyncMock(return_value=result)
        yield session

    async def fake_identity(code):
        return {"userid": "lisi", "email": "wecom.lisi@wecom.local", "name": "李四"}

    async def fake_log(*args, **kwargs):
        pass

    app_ = FastAPI()
    app_.include_router(wecom_mod.router)
    app_.dependency_overrides[get_session] = fake_get_session

    monkeypatch.setattr(wecom_mod, "_fetch_wecom_identity", fake_identity)
    monkeypatch.setattr("app.views.wecom_auth.log_operation", fake_log)

    with TestClient(app_) as c:
        yield c


def _get_state(exchange_client) -> str:
    r = exchange_client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:5173/console"},
    )
    url = r.json()["authorize_url"]
    return url.split("state=")[1].split("&")[0]


def test_exchange_returns_jwt(exchange_client):
    state = _get_state(exchange_client)
    r = exchange_client.post(
        "/auth/wecom/exchange",
        json={"code": "mycode", "state": state},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "wecom.lisi@wecom.local"
    assert exchange_client.cookies.get("wecom_oauth_state") is None


def test_browser_start_survives_reconnect_and_clears_state_after_exchange(exchange_client):
    from urllib.parse import parse_qs, urlsplit

    started = exchange_client.get(
        "/auth/wecom/start",
        params={"redirect_uri": "http://localhost:5173/console", "flow": "qr",
                "return_query": "page=%E6%95%B0%E6%8D%AE%E6%B5%8F%E8%A7%88&orders_platform=%E4%BA%AC%E4%B8%9C"},
        follow_redirects=False,
    )
    assert started.status_code == 302
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    assert exchange_client.cookies.get("wecom_oauth_state") == state

    # A new Streamlit WebSocket session uses the browser's cookie, not the
    # original Python API client session.
    completed = exchange_client.post("/auth/wecom/exchange", json={"code": "mycode", "state": state})
    assert completed.status_code == 200
    assert completed.json()["access_token"]
    assert exchange_client.cookies.get("wecom_oauth_state") is None
    assert exchange_client.cookies.get("dashboard_login_return") is not None


def test_exchange_rejects_state_from_another_login_session(exchange_client):
    state = _get_state(exchange_client)
    exchange_client.cookies.clear()
    r = exchange_client.post(
        "/auth/wecom/exchange", json={"code": "mycode", "state": state},
    )
    assert r.status_code == 400


def test_exchange_rejects_replayed_state(exchange_client):
    state = _get_state(exchange_client)
    first = exchange_client.post("/auth/wecom/exchange", json={"code": "first", "state": state})
    second = exchange_client.post("/auth/wecom/exchange", json={"code": "second", "state": state})
    assert first.status_code == 200
    assert second.status_code == 400


def test_exchange_invalid_state_rejected(exchange_client):
    r = exchange_client.post(
        "/auth/wecom/exchange",
        json={"code": "mycode", "state": "bad.state"},
    )
    assert r.status_code == 400


def test_exchange_expired_state_rejected(exchange_client):
    m = _wecom_module()
    old_state = m._sign_state({"ts": int(time.time()) - 700, "nonce": "x"})
    r = exchange_client.post(
        "/auth/wecom/exchange",
        json={"code": "mycode", "state": old_state},
    )
    assert r.status_code == 400


def test_exchange_inactive_user_rejected(monkeypatch):
    _set_wecom_env(monkeypatch)

    import app.views.wecom_auth as wecom_mod
    from app.db import get_session

    fake_user = MagicMock()
    fake_user.is_active = False

    async def fake_get_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = fake_user
        session.execute = AsyncMock(return_value=result)
        yield session

    async def fake_identity(code):
        return {"userid": "inactive", "email": "wecom.inactive@wecom.local", "name": "X"}

    app_ = FastAPI()
    app_.include_router(wecom_mod.router)
    app_.dependency_overrides[get_session] = fake_get_session
    monkeypatch.setattr(wecom_mod, "_fetch_wecom_identity", fake_identity)

    with TestClient(app_) as c:
        state = _get_state(c)
        r = c.post(
            "/auth/wecom/exchange",
            json={"code": "code", "state": state},
        )
    assert r.status_code == 403

@pytest.mark.parametrize("redirect", [
    "https://dashboard.example.com.evil.example",
    "https://dashboard.example.com@evil.example",
    "https://dashboard.example.com:444",
    "https://dashboard.example.com:0",
    "https://dashboard.example.com/unconfigured-callback",
    "https://dashboard.example.com?next=https://evil.example",
    "https://dashboard.example.com#fragment",
    "http://dashboard.example.com",
    "https://dashboard.example.com:invalid",
])
def test_authorize_rejects_callback_allowlist_bypasses(wecom_client, monkeypatch, redirect):
    _set_wecom_env(monkeypatch)
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://dashboard.example.com")
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("STREAMLIT_URL", raising=False)
    response = wecom_client.get("/auth/wecom/authorize-url", params={"redirect_uri": redirect})
    assert response.status_code == 400


@pytest.mark.parametrize("redirect", ["https://dashboard.example.com/", "https://dashboard.example.com:443"])
def test_authorize_accepts_equivalent_configured_callback(wecom_client, monkeypatch, redirect):
    _set_wecom_env(monkeypatch)
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://dashboard.example.com")
    response = wecom_client.get("/auth/wecom/authorize-url", params={"redirect_uri": redirect})
    assert response.status_code == 200


@pytest.mark.parametrize("callback,expected", [
    ("https://dashboard.example.com/console/", 200),
    ("https://dashboard.example.com/console/other", 400),
    ("https://dashboard.example.com/console-evil", 400),
    ("https://evil.test/console/", 400),
])
def test_console_callback_is_explicit_and_path_scoped(wecom_client, monkeypatch, callback, expected):
    monkeypatch.setenv("WECOM_CONSOLE_REDIRECT_URI", "https://dashboard.example.com/console/")
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://dashboard.example.com")
    monkeypatch.setenv("WECOM_CORP_ID", "corp")
    monkeypatch.setenv("WECOM_AGENT_ID", "agent")
    monkeypatch.setenv("WECOM_APP_SECRET", "secret")
    result = wecom_client.get("/auth/wecom/authorize-url", params={"redirect_uri": callback})
    assert result.status_code == expected
    # Adding the React callback must retain the existing root callback.
    legacy = wecom_client.get("/auth/wecom/authorize-url", params={"redirect_uri": "https://dashboard.example.com"})
    assert legacy.status_code == 200

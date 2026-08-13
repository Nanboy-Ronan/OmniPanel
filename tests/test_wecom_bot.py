"""Tests for app/utils/wecom_bot.py.

Most tests here cover HTTP payload plumbing only — no real HTTP calls, no DB
— and stub out _resolve_touser() so they don't depend on WECOM_ALERT_TOUSER
being unset AND no real local Postgres having an opted-in user (both would
otherwise change the resolved touser under a developer's real .env).
Recipient-resolution behavior itself (env override vs. DB opt-in vs. "@all")
is covered separately in TestResolveTouser using the isolated test DB.

Alerting sends via the existing WeCom self-built app (WECOM_CORP_ID /
WECOM_AGENT_ID / WECOM_APP_SECRET — the same ones used for OAuth login),
not a group-bot webhook: group-bot webhooks require a permission this org
doesn't have.
"""
from __future__ import annotations

import httpx
import pytest

from app.utils import wecom_bot


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {"errcode": 0}
        self.text = text

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _set_wecom_env(monkeypatch, corpid="corp1", agentid="1000006", secret="s3cr3t", touser=None):
    monkeypatch.setenv("WECOM_CORP_ID", corpid)
    monkeypatch.setenv("WECOM_AGENT_ID", agentid)
    monkeypatch.setenv("WECOM_APP_SECRET", secret)
    if touser is not None:
        monkeypatch.setenv("WECOM_ALERT_TOUSER", touser)
    else:
        monkeypatch.delenv("WECOM_ALERT_TOUSER", raising=False)


def _clear_wecom_env(monkeypatch):
    for name in ("WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_APP_SECRET", "WECOM_ALERT_TOUSER"):
        monkeypatch.delenv(name, raising=False)


def test_noop_when_unconfigured(monkeypatch):
    _clear_wecom_env(monkeypatch)
    called = {}

    def _fake_get(*a, **kw):
        called["hit"] = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", _fake_get)
    assert wecom_bot.send_wecom_alert("hello") is False
    assert "hit" not in called


@pytest.mark.parametrize("missing", ["WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_APP_SECRET"])
def test_noop_when_partially_configured(monkeypatch, missing):
    _set_wecom_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    assert wecom_bot.send_wecom_alert("hello") is False


def test_sends_expected_payload_default_touser(monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setattr(wecom_bot, "_resolve_touser", lambda: "@all")
    captured = {}

    def _fake_get(url, params=None, timeout=None):
        assert url == wecom_bot._GET_TOKEN_URL
        assert params == {"corpid": "corp1", "corpsecret": "s3cr3t"}
        return _FakeResponse(json_body={"access_token": "tok123", "errcode": 0})

    def _fake_post(url, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", _fake_get)
    monkeypatch.setattr(httpx, "post", _fake_post)

    assert wecom_bot.send_wecom_alert("采集失败") is True
    assert captured["url"] == wecom_bot._SEND_MESSAGE_URL
    assert captured["params"] == {"access_token": "tok123"}
    assert captured["json"] == {
        "touser": "@all",
        "msgtype": "text",
        "agentid": 1000006,
        "text": {"content": "采集失败"},
    }


def test_respects_custom_touser(monkeypatch):
    _set_wecom_env(monkeypatch, touser="alice|bob")
    captured = {}

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(json_body={"access_token": "tok123"}))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: captured.setdefault("json", kw["json"]) or _FakeResponse())

    wecom_bot.send_wecom_alert("x")
    assert captured["json"]["touser"] == "alice|bob"


def test_returns_false_when_token_fetch_fails(monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setattr(wecom_bot, "_resolve_touser", lambda: "@all")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(json_body={"errcode": 40001, "errmsg": "bad secret"}))
    called = {}
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: called.setdefault("hit", True) or _FakeResponse())
    assert wecom_bot.send_wecom_alert("x") is False
    assert "hit" not in called


def test_returns_false_on_send_non_200(monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setattr(wecom_bot, "_resolve_touser", lambda: "@all")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(json_body={"access_token": "tok123"}))
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(status_code=500, text="boom"))
    assert wecom_bot.send_wecom_alert("x") is False


def test_returns_false_on_wecom_errcode(monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setattr(wecom_bot, "_resolve_touser", lambda: "@all")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(json_body={"access_token": "tok123"}))
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(json_body={"errcode": 93000, "errmsg": "bad agentid"}))
    assert wecom_bot.send_wecom_alert("x") is False


def test_never_raises_on_network_error(monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setattr(wecom_bot, "_resolve_touser", lambda: "@all")

    def _raise(*a, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "get", _raise)
    assert wecom_bot.send_wecom_alert("x") is False


class TestResolveTouser:
    """DB-backed coverage for _resolve_touser()'s actual priority logic:
    env override > DB opt-in list > "@all". Uses the isolated per-session
    test Postgres DB (via the `client` fixture's app.db.SyncSessionLocal
    monkeypatch), not a developer's real local database.
    """

    @pytest.fixture
    def wired_db(self, pg_sync_url, monkeypatch):
        """Point app.db.SyncSessionLocal at the isolated test DB (same
        monkeypatch idiom as tests/test_api_endpoints.py's `client` fixture)
        and hand back a session factory for seeding User rows."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker, Session
        import app.db as db

        engine = create_engine(pg_sync_url, future=True)
        SessionLocal = sessionmaker(engine, class_=Session, expire_on_commit=False)
        monkeypatch.setattr(db, "SyncSessionLocal", SessionLocal, raising=False)
        yield SessionLocal
        engine.dispose()

    def _make_user(self, session_factory, *, email, wecom_userid=None, wecom_alert_enabled=False):
        from app.db.models import User
        with session_factory() as s:
            u = User(
                email=email, hashed_password="x",
                wecom_userid=wecom_userid, wecom_alert_enabled=wecom_alert_enabled,
            )
            s.add(u)
            s.commit()

    def test_no_opted_in_users_falls_back_to_all(self, monkeypatch, wired_db):
        monkeypatch.delenv("WECOM_ALERT_TOUSER", raising=False)
        self._make_user(wired_db, email="a@example.com", wecom_userid="uidA", wecom_alert_enabled=False)
        assert wecom_bot._resolve_touser() == "@all"

    def test_opted_in_users_are_joined_by_pipe(self, monkeypatch, wired_db):
        monkeypatch.delenv("WECOM_ALERT_TOUSER", raising=False)
        self._make_user(wired_db, email="a@example.com", wecom_userid="uidA", wecom_alert_enabled=True)
        self._make_user(wired_db, email="b@example.com", wecom_userid="uidB", wecom_alert_enabled=True)
        self._make_user(wired_db, email="c@example.com", wecom_userid=None, wecom_alert_enabled=True)
        self._make_user(wired_db, email="d@example.com", wecom_userid="uidD", wecom_alert_enabled=False)
        touser = wecom_bot._resolve_touser()
        assert set(touser.split("|")) == {"uidA", "uidB"}

    def test_env_override_wins_even_with_opted_in_users(self, monkeypatch, wired_db):
        monkeypatch.setenv("WECOM_ALERT_TOUSER", "override_id")
        self._make_user(wired_db, email="a@example.com", wecom_userid="uidA", wecom_alert_enabled=True)
        assert wecom_bot._resolve_touser() == "override_id"

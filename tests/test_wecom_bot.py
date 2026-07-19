"""Tests for app/utils/wecom_bot.py — no real HTTP calls, no DB.

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
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(json_body={"errcode": 40001, "errmsg": "bad secret"}))
    called = {}
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: called.setdefault("hit", True) or _FakeResponse())
    assert wecom_bot.send_wecom_alert("x") is False
    assert "hit" not in called


def test_returns_false_on_send_non_200(monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(json_body={"access_token": "tok123"}))
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(status_code=500, text="boom"))
    assert wecom_bot.send_wecom_alert("x") is False


def test_returns_false_on_wecom_errcode(monkeypatch):
    _set_wecom_env(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(json_body={"access_token": "tok123"}))
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(json_body={"errcode": 93000, "errmsg": "bad agentid"}))
    assert wecom_bot.send_wecom_alert("x") is False


def test_never_raises_on_network_error(monkeypatch):
    _set_wecom_env(monkeypatch)

    def _raise(*a, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "get", _raise)
    assert wecom_bot.send_wecom_alert("x") is False

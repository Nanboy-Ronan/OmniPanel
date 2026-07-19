"""Tests for app/utils/wecom_bot.py — no real HTTP calls, no DB."""
from __future__ import annotations

import httpx
import pytest

from app.utils import wecom_bot


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body or {"errcode": 0}
        self.text = text

    def json(self):
        return self._json_body


def test_noop_when_webhook_unset(monkeypatch):
    monkeypatch.setattr(wecom_bot.settings, "wecom_bot_webhook", None, raising=False)
    called = {}

    def _fake_post(*a, **kw):
        called["hit"] = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", _fake_post)
    assert wecom_bot.send_wecom_alert("hello") is False
    assert "hit" not in called


def test_sends_expected_payload(monkeypatch):
    monkeypatch.setattr(wecom_bot.settings, "wecom_bot_webhook", "https://example.com/hook", raising=False)
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", _fake_post)
    assert wecom_bot.send_wecom_alert("采集失败") is True
    assert captured["url"] == "https://example.com/hook"
    assert captured["json"] == {"msgtype": "text", "text": {"content": "采集失败"}}


def test_returns_false_on_non_200(monkeypatch):
    monkeypatch.setattr(wecom_bot.settings, "wecom_bot_webhook", "https://example.com/hook", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(status_code=500, text="boom"))
    assert wecom_bot.send_wecom_alert("x") is False


def test_returns_false_on_wecom_errcode(monkeypatch):
    monkeypatch.setattr(wecom_bot.settings, "wecom_bot_webhook", "https://example.com/hook", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(json_body={"errcode": 93000, "errmsg": "bad key"}))
    assert wecom_bot.send_wecom_alert("x") is False


def test_never_raises_on_network_error(monkeypatch):
    monkeypatch.setattr(wecom_bot.settings, "wecom_bot_webhook", "https://example.com/hook", raising=False)

    def _raise(*a, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "post", _raise)
    assert wecom_bot.send_wecom_alert("x") is False

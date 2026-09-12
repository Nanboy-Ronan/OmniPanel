"""Tests for app/collector/pugongying.py's verify_pugongying_session().

Mirrors tests/test_collector_xhs.py's fake-page approach: no real
Chromium/Playwright, open_context() is monkeypatched to yield a fake page
whose .url changes across polls.
"""
from __future__ import annotations

import contextlib

from app.collector import pugongying


class _FakePage:
    """Simulates the URL a browser would report across successive polls.

    `url_sequence` is the sequence of .url values observed on each
    wait_for_timeout() poll after a goto(); the last entry repeats once
    exhausted (models a state that never changes again).
    """

    def __init__(self, url_sequence: list[str]):
        self._sequence = url_sequence
        self._idx = 0
        self.url = url_sequence[0]

    def goto(self, url, wait_until=None):
        self._idx = 0
        self.url = self._sequence[0]

    def wait_for_timeout(self, ms):
        self._idx += 1
        self.url = self._sequence[min(self._idx, len(self._sequence) - 1)]

    def inner_text(self, selector):
        return ""


def _fake_open_context_factory(page):
    @contextlib.contextmanager
    def _fake_open_context(storage_path, headless=None):
        yield page
    return _fake_open_context


class TestVerifyPugongyingSession:
    def test_valid_session_returns_true(self, tmp_path, monkeypatch):
        page = _FakePage(["https://pgy.xiaohongshu.com/solar/post-trade/content-manage"])
        monkeypatch.setattr(pugongying, "open_context", _fake_open_context_factory(page))

        assert pugongying.verify_pugongying_session(tmp_path / "session.json") is True

    def test_genuinely_expired_session_returns_false(self, tmp_path, monkeypatch):
        # Stays on a login-looking URL for the entire wait budget.
        page = _FakePage(["https://pgy.xiaohongshu.com/login"])
        monkeypatch.setattr(pugongying, "open_context", _fake_open_context_factory(page))

        assert pugongying.verify_pugongying_session(tmp_path / "session.json") is False

    def test_session_saved_before_role_selected_is_reported_expired(self, tmp_path, monkeypatch):
        # A session bootstrapped before bootstrap-login's role-select fix
        # existed (or one saved mid-flow some other way): every visit
        # redirects straight back to the role-select page. This must be
        # reported the same as a dead session (re-run bootstrap-login), not
        # silently treated as valid only to time out later on the export
        # button click (the original bug -- see pugongying.py docstring).
        page = _FakePage(["https://pgy.xiaohongshu.com/role-select?xxx=1"])
        monkeypatch.setattr(pugongying, "open_context", _fake_open_context_factory(page))

        assert pugongying.verify_pugongying_session(tmp_path / "session.json") is False

    def test_transient_role_select_flicker_then_resolves_is_still_valid(self, tmp_path, monkeypatch):
        # Guard against over-tightening: a role-select URL seen only
        # transiently (e.g. briefly during a redirect chain) and then gone
        # within the wait budget must not be misreported as expired --
        # mirrors the XHS CAS-handoff transient-redirect test.
        page = _FakePage([
            "https://pgy.xiaohongshu.com/role-select?xxx=1",
            "https://pgy.xiaohongshu.com/solar/post-trade/content-manage",
        ])
        monkeypatch.setattr(pugongying, "open_context", _fake_open_context_factory(page))

        assert pugongying.verify_pugongying_session(tmp_path / "session.json") is True

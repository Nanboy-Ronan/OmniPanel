"""Tests for app/collector/xhs.py's verify_xhs_session().

No real Chromium/Playwright: open_context() is monkeypatched to yield a fake
page whose .url changes across polls, letting us exercise the CAS-handoff
wait budget (_goto_and_check_login) without a live portal.
"""
from __future__ import annotations

import contextlib

from app.collector import xhs


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
    """Returns a stand-in for browser.open_context() that yields `page`."""
    @contextlib.contextmanager
    def _fake_open_context(storage_path, headless=None):
        yield page
    return _fake_open_context


class TestVerifyXhsSession:
    def test_valid_session_returns_true(self, tmp_path, monkeypatch):
        page = _FakePage(["https://creator.xiaohongshu.com/statistics/data-analysis"])
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        assert xhs.verify_xhs_session(tmp_path / "session.json") is True

    def test_transient_cas_redirect_resolves_within_budget(self, tmp_path, monkeypatch):
        # Login-looking for the first few polls, then resolves — models the
        # documented ~6s silent CAS ticket exchange, well inside the 15s budget.
        page = _FakePage([
            "https://pro.xiaohongshu.com/login",
            "https://pro.xiaohongshu.com/login",
            "https://pro.xiaohongshu.com/login",
            "https://creator.xiaohongshu.com/statistics/data-analysis",
        ])
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        assert xhs.verify_xhs_session(tmp_path / "session.json") is True

    def test_genuinely_expired_session_returns_false(self, tmp_path, monkeypatch):
        # Stays on a login-looking URL for the entire wait budget.
        page = _FakePage(["https://pro.xiaohongshu.com/login"])
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        assert xhs.verify_xhs_session(tmp_path / "session.json") is False

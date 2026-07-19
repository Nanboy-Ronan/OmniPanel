"""Tests for app/collector/browser.py's session-file atomic write.

No real Chromium/Playwright involved — _save_storage_state_atomic only
needs an object with a .storage_state() method, so we fake that directly.
"""
from __future__ import annotations

import json
import stat

from app.collector.browser import _save_storage_state_atomic


class _FakeContext:
    def __init__(self, state=None, raise_on_read=False):
        self._state = state if state is not None else {"cookies": [], "origins": []}
        self._raise_on_read = raise_on_read

    def storage_state(self):
        if self._raise_on_read:
            raise RuntimeError("boom")
        return self._state


class TestSaveStorageStateAtomic:
    def test_writes_valid_json_with_0600_perms(self, tmp_path):
        path = tmp_path / "xhs_1.json"
        state = {"cookies": [{"name": "a", "value": "b"}], "origins": []}

        _save_storage_state_atomic(_FakeContext(state), path)

        assert path.exists()
        assert json.loads(path.read_text()) == state
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_no_leftover_tmp_file_on_success(self, tmp_path):
        path = tmp_path / "xhs_1.json"
        _save_storage_state_atomic(_FakeContext(), path)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_failure_does_not_raise_and_cleans_up_tmp(self, tmp_path):
        path = tmp_path / "xhs_1.json"
        # _save_storage_state_atomic must never raise — a broken save must not
        # crash the collector run/teardown, see its docstring.
        _save_storage_state_atomic(_FakeContext(raise_on_read=True), path)
        assert not path.exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_failure_leaves_existing_file_untouched(self, tmp_path):
        path = tmp_path / "xhs_1.json"
        path.write_text('{"cookies": "original"}')

        _save_storage_state_atomic(_FakeContext(raise_on_read=True), path)

        assert path.read_text() == '{"cookies": "original"}'

    def test_replaces_existing_file(self, tmp_path):
        path = tmp_path / "xhs_1.json"
        path.write_text('{"cookies": "old"}')
        new_state = {"cookies": [{"name": "rotated", "value": "new"}], "origins": []}

        _save_storage_state_atomic(_FakeContext(new_state), path)

        assert json.loads(path.read_text()) == new_state

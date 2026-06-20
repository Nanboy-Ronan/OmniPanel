"""Tests for single-leader election (app/utils/leader.py)."""
import os
import tempfile

import pytest

from app.utils import leader as _leader_mod
from app.utils.leader import try_become_leader, release


@pytest.fixture(autouse=True)
def _reset_leader():
    """Ensure each test starts with no held lock handle."""
    release()
    yield
    release()


def test_first_caller_becomes_leader(tmp_path):
    lock = str(tmp_path / "test.lock")
    assert try_become_leader(lock) is True


def test_second_caller_is_not_leader(tmp_path):
    lock = str(tmp_path / "test.lock")
    assert try_become_leader(lock) is True
    # A second call in the same process re-uses the open fd — releasing first
    # lets us simulate a second independent process acquiring attempt cleanly.
    # Instead, open the same lock in a fresh file descriptor context.
    try:
        import fcntl
    except ImportError:
        pytest.skip("fcntl not available on this platform")

    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # If we get here, the lock is NOT held — that's a failure.
        os.close(fd)
        pytest.fail("Second open should have failed to acquire exclusive lock")
    except OSError:
        # Expected: first caller still holds the lock.
        os.close(fd)


def test_release_allows_reacquisition(tmp_path):
    lock = str(tmp_path / "test.lock")
    assert try_become_leader(lock) is True
    release()
    assert try_become_leader(lock) is True


def test_leader_writes_pid_to_lock_file(tmp_path):
    lock = str(tmp_path / "test.lock")
    try_become_leader(lock)
    pid_in_file = open(lock).read().strip()
    assert pid_in_file == str(os.getpid())


def test_default_lock_path_uses_env_override(tmp_path, monkeypatch):
    custom = str(tmp_path / "custom.lock")
    monkeypatch.setenv("RAP_LEADER_LOCK_PATH", custom)
    from app.utils.leader import default_lock_path
    assert default_lock_path() == custom


def test_no_fcntl_always_leader(monkeypatch):
    monkeypatch.setattr(_leader_mod, "fcntl", None)
    assert try_become_leader() is True

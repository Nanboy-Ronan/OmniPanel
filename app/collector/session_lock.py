"""Cross-process locks shared by collectors and login repair (Linux server)."""
import contextlib
import os
from pathlib import Path

@contextlib.contextmanager
def session_lock(path: Path, *, blocking=True):
    import fcntl
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def acquire_lock(path: Path, timeout: float, *, interval: float = 1.0, clock=None, sleep=None):
    """Wait up to *timeout* seconds for the session lock; return a held fd or None.

    For callers that must hold the lock across awaits (login repair installation):
    release with ``os.close(fd)``. Waiting briefly avoids discarding an employee's
    fresh login just because a collect of the same account is finishing.
    """
    import fcntl
    import time
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = clock() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if clock() >= deadline:
                os.close(fd)
                return None
            sleep(interval)

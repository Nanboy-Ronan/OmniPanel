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

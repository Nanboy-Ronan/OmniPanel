"""Cross-worker leader election via an advisory file lock.

uvicorn runs multiple worker processes (``--workers N``); each imports the app
and runs the FastAPI lifespan independently, so an ``asyncio.create_task`` in
lifespan would start the background loops *once per worker*. The monthly-backup
and WeChat auto-sync loops must run in exactly one process.

``try_become_leader`` uses a non-blocking ``flock``: the first worker to start
wins the lock and runs the loops; every other worker fails the lock and skips
them. The lock is advisory and tied to the open file description, so it is held
for as long as the leader keeps the handle open and is released automatically
when that process exits — at which point a restarted worker can take over.

On platforms without ``fcntl`` (Windows dev) there is effectively a single
process, so the caller is always treated as the leader.
"""
from __future__ import annotations

import logging
import os
import tempfile

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (Windows/dev)
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Holding the handle at module scope keeps the file descriptor (and therefore
# the flock) alive for the whole process lifetime; letting it be garbage
# collected would close the fd and release the lock.
_leader_handle = None


def default_lock_path() -> str:
    """Return the leader-lock path, overridable via ``RAP_LEADER_LOCK_PATH``."""
    override = os.getenv("RAP_LEADER_LOCK_PATH")
    if override:
        return override
    return os.path.join(tempfile.gettempdir(), "rpa-leader.lock")


def try_become_leader(lock_path: str | None = None) -> bool:
    """Attempt to become the single leader worker for background loops.

    Returns ``True`` if this process acquired the leader lock (and should run
    the background loops), ``False`` if another worker already holds it.
    """
    global _leader_handle

    if fcntl is None:
        logger.info("fcntl unavailable; assuming single-process leader")
        return True

    path = lock_path or default_lock_path()
    # O_CLOEXEC: don't leak the lock fd into subprocesses (e.g. pg_dump).
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    handle = os.fdopen(fd, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        logger.info(
            "Background-loop leader already elected elsewhere; this worker will skip loops"
        )
        return False

    # Record the leader pid for debugging; best-effort.
    try:
        handle.seek(0)
        handle.truncate(0)
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass

    _leader_handle = handle
    logger.info("Elected as background-loop leader (pid=%s, lock=%s)", os.getpid(), path)
    return True


def release() -> None:
    """Release the leader lock if held. Primarily for tests and clean shutdown."""
    global _leader_handle
    if _leader_handle is not None:
        try:
            _leader_handle.close()
        finally:
            _leader_handle = None

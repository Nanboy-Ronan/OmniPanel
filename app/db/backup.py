from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (e.g. Windows dev)
    fcntl = None  # type: ignore[assignment]

from . import DATABASE_URL
from ..config import settings

logger = logging.getLogger(__name__)

_BACKUP_EXTENSIONS = {".sql", ".gz", ".dump", ".db"}


def prune_old_backups(backup_dir: Path, keep: int | None = None) -> int:
    """Delete oldest backup files beyond *keep* most-recent, return count removed.

    Only files whose suffix is in ``_BACKUP_EXTENSIONS`` are considered.
    Dotfiles (e.g. ``.last_monthly_backup``, ``.backup.lock``) are always left
    alone.  *keep* defaults to ``settings.rpa_backup_keep`` (5).
    """
    if keep is None:
        keep = settings.rpa_backup_keep

    candidates = sorted(
        (
            f
            for f in backup_dir.iterdir()
            if f.is_file()
            and not f.name.startswith(".")
            and f.suffix in _BACKUP_EXTENSIONS
        ),
        key=lambda f: f.stat().st_mtime,
        reverse=True,  # newest first
    )

    to_delete = candidates[keep:]
    for path in to_delete:
        try:
            path.unlink()
            logger.info("Pruned old backup: %s", path.name)
        except OSError as exc:
            logger.warning("Could not prune %s: %s", path.name, exc)

    return len(to_delete)


def _last_backup_file(backup_dir: Path) -> Path:
    return backup_dir / ".last_monthly_backup"


def _read_last_backup(backup_dir: Path) -> datetime | None:
    stamp_file = _last_backup_file(backup_dir)
    if not stamp_file.exists():
        return None
    try:
        ts = stamp_file.read_text().strip()
        return datetime.fromisoformat(ts)
    except (ValueError, OSError):
        return None


def _write_last_backup(backup_dir: Path) -> None:
    stamp_file = _last_backup_file(backup_dir)
    stamp_file.write_text(datetime.now().isoformat())


def monthly_backup(backup_dir: str | Path | None = None) -> Path | None:
    """Run a backup if the last monthly backup was >= 30 days ago.

    Guarded by a cross-process file lock: under multiple uvicorn workers each
    process runs its own backup loop, and without the lock two workers would
    fire ``pg_dump`` into the same timestamped file simultaneously, producing a
    corrupt (doubled) dump. Only the worker that wins the lock performs the
    backup; the others skip.
    """
    root = Path(backup_dir or settings.rpa_backup_dir)
    root.mkdir(parents=True, exist_ok=True)

    # Fast pre-check before taking the lock / opening pg_dump.
    last = _read_last_backup(root)
    if last is not None and datetime.now() - last < timedelta(days=30):
        return None

    lock_fh = open(root / ".backup.lock", "w")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                logger.info(
                    "Monthly backup: lock held by another worker — skipping"
                )
                return None

        # Re-check under the lock: the worker that ran first may have just
        # finished and written the stamp.
        last = _read_last_backup(root)
        if last is not None and datetime.now() - last < timedelta(days=30):
            return None

        result = backup_database("monthly", backup_dir=root)
        if result:
            _write_last_backup(root)
            pruned = prune_old_backups(root)
            if pruned:
                logger.info("Pruned %d old backup(s) from %s", pruned, root)
        return result
    finally:
        lock_fh.close()  # releases the flock


def _pg_connection_args(parsed) -> list[str]:
    """Build the -h/-p/-U flags shared by pg_dump and psql."""
    args: list[str] = []
    if parsed.hostname:
        args.extend(["-h", parsed.hostname])
    if parsed.port:
        args.extend(["-p", str(parsed.port)])
    if parsed.username:
        args.extend(["-U", parsed.username])
    return args


def _wrap_for_docker(pg_args: list[str], parsed, *, interactive: bool) -> tuple[list[str], dict]:
    """Return the (argv, env) to run a postgres client command.

    When ``settings.rpa_pg_docker_container`` is set, the command is wrapped in
    ``docker exec`` so it runs inside the PostgreSQL container (where the
    client binaries live). Input/output is streamed over stdin/stdout, so the
    dump file always lands on the host filesystem regardless of where the
    binary runs. Otherwise the command runs natively and ``PGPASSWORD`` is
    passed through the environment.
    """
    container = settings.rpa_pg_docker_container
    env = os.environ.copy()
    if container:
        argv = ["docker", "exec"]
        if interactive:
            argv.append("-i")
        if parsed.password:
            argv.extend(["-e", f"PGPASSWORD={parsed.password}"])
        argv.append(container)
        argv.extend(pg_args)
    else:
        argv = pg_args
        if parsed.password:
            env["PGPASSWORD"] = parsed.password
    return argv, env


def backup_database(reason: str, backup_dir: str | Path | None = None) -> Path | None:
    """Create a database backup using ``pg_dump`` and return its path."""
    root = Path(backup_dir or settings.rpa_backup_dir)
    root.mkdir(parents=True, exist_ok=True)

    safe_reason = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in reason
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    parsed = urlparse(DATABASE_URL)
    db_name = parsed.path.lstrip("/") or "rpa"
    backup_path = root / f"{db_name}-{timestamp}-{safe_reason}.sql"

    # Dump to stdout (no -f) so the file is written on the host even when the
    # command runs inside the database container.
    pg_args = ["pg_dump", "--clean", "--if-exists", "--no-owner", "--no-acl"]
    pg_args.extend(_pg_connection_args(parsed))
    pg_args.append(db_name)
    argv, env = _wrap_for_docker(pg_args, parsed, interactive=False)

    try:
        with open(backup_path, "wb") as fh:
            subprocess.run(
                argv, env=env, check=True, stdout=fh, stderr=subprocess.PIPE, text=True
            )
        return backup_path
    except FileNotFoundError as exc:
        backup_path.unlink(missing_ok=True)
        logger.error(
            "pg_dump/docker not found; set RPA_PG_DOCKER_CONTAINER or install "
            "PostgreSQL client tools",
            exc_info=exc,
        )
        return None
    except subprocess.CalledProcessError as exc:
        # Don't leave a truncated/empty dump behind on failure.
        backup_path.unlink(missing_ok=True)
        logger.error(
            "pg_dump failed (exit %s): %s", exc.returncode, exc.stderr, exc_info=exc
        )
        return None


def restore_database(backup_path: str | Path) -> bool:
    """Restore a SQL backup into ``DATABASE_URL`` using ``psql``."""
    path = Path(backup_path)
    if not path.exists():
        return False

    parsed = urlparse(DATABASE_URL)
    db_name = parsed.path.lstrip("/") or "rpa"

    # Read the dump from stdin so it works whether psql runs natively or inside
    # the database container.
    pg_args = ["psql", "-v", "ON_ERROR_STOP=1"]
    pg_args.extend(_pg_connection_args(parsed))
    pg_args.append(db_name)
    argv, env = _wrap_for_docker(pg_args, parsed, interactive=True)

    try:
        with open(path, "rb") as fh:
            subprocess.run(
                argv, env=env, check=True, stdin=fh, stderr=subprocess.PIPE, text=True
            )
        return True
    except FileNotFoundError as exc:
        logger.error(
            "psql/docker not found; set RPA_PG_DOCKER_CONTAINER or install "
            "PostgreSQL client tools",
            exc_info=exc,
        )
        return False
    except subprocess.CalledProcessError as exc:
        logger.error(
            "psql restore failed (exit %s): %s", exc.returncode, exc.stderr, exc_info=exc
        )
        return False


# Keep backward-compatible alias
backup_sqlite = backup_database

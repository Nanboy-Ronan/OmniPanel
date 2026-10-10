"""PostgreSQL dumps: scheduled daily/monthly backups, the pre-migration
backup taken by deploy.sh, an optional off-site hook, and a restore drill.

CLI (see docs/maintenance.md)::

    python -m app.db.backup backup <reason> [--backup-dir DIR] [--no-offsite]
    python -m app.db.backup verify <dump>
    python -m app.db.backup restore-drill [--dump PATH] [--backup-dir DIR]
"""
from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
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

# Every plain-format pg_dump starts with this banner within its first lines.
_DUMP_HEADER_MARKER = b"PostgreSQL database dump"
_DUMP_HEADER_BYTES = 4096


def _ensure_private_dir(path: Path) -> Path:
    """Create *path* (0700) and tighten an existing one: dumps contain every
    customer row and credential column, so the directory must not be
    listable/readable by other local users."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError as exc:  # e.g. a dir owned by another user
        logger.warning("Could not restrict permissions on %s: %s", path, exc)
    return path


def validate_dump(path: str | Path) -> str | None:
    """Return a problem description when *path* is not a usable plain-SQL
    pg_dump (missing, empty, unreadable, or without the pg_dump header);
    None when it looks valid."""
    p = Path(path)
    try:
        size = p.stat().st_size
        if size == 0:
            return f"{p.name} 是空文件（0 字节）"
        with open(p, "rb") as fh:
            head = fh.read(_DUMP_HEADER_BYTES)
    except OSError as exc:
        return f"{p.name} 无法读取：{exc}"
    if _DUMP_HEADER_MARKER not in head:
        return f"{p.name} 缺少 pg_dump 文件头，可能已损坏或被截断"
    return None


def latest_dump(backup_dir: str | Path) -> Path | None:
    """Newest backup file in *backup_dir* (by mtime), or None."""
    root = Path(backup_dir)
    if not root.is_dir():
        return None
    files = [
        f for f in root.iterdir()
        if f.is_file() and not f.name.startswith(".") and f.suffix in _BACKUP_EXTENSIONS
    ]
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


def run_offsite_hook(backup_path: Path) -> bool:
    """Run BACKUP_OFFSITE_COMMAND with the dump path as its last argument
    (e.g. an rclone/rsync/ossutil wrapper script). No-op (True) when unset.
    A failure or timeout is logged and alerted via WeCom but does not fail
    the local backup itself. Returns False only on a failed hook."""
    command = (settings.backup_offsite_command or "").strip()
    if not command:
        return True
    argv = shlex.split(command) + [str(backup_path)]
    timeout = settings.backup_offsite_timeout_seconds
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        problem = None if result.returncode == 0 else (
            f"退出码 {result.returncode}：{(result.stderr or result.stdout or '').strip()[-500:]}"
        )
    except subprocess.TimeoutExpired:
        problem = f"超过 {timeout} 秒未完成"
    except OSError as exc:
        problem = f"无法执行：{exc}"
    if problem is None:
        logger.info("Off-site backup hook succeeded for %s", backup_path.name)
        return True
    logger.error("Off-site backup hook failed for %s: %s", backup_path.name, problem)
    from ..utils.wecom_bot import send_wecom_alert

    send_wecom_alert(f"[备份告警] 异地备份命令失败（{backup_path.name}）：{problem}")
    return False


def prune_old_backups(backup_dir: Path, keep: int | None = None, reason: str | None = None) -> int:
    """Delete oldest backup files beyond *keep* most-recent, return count removed.

    Only files whose suffix is in ``_BACKUP_EXTENSIONS`` are considered.
    Dotfiles (e.g. ``.last_monthly_backup``, ``.backup.lock``) are always left
    alone.  *keep* defaults to ``settings.backup_keep`` (5).
    """
    if keep is None:
        keep = settings.backup_keep

    candidates = sorted(
        (
            f
            for f in backup_dir.iterdir()
            if f.is_file()
            and not f.name.startswith(".")
            and f.suffix in _BACKUP_EXTENSIONS
            and (reason is None or f.stem.endswith(f"-{reason}"))
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
    root = _ensure_private_dir(Path(backup_dir or settings.backup_dir))

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
            pruned = prune_old_backups(root, reason="monthly")
            if pruned:
                logger.info("Pruned %d old backup(s) from %s", pruned, root)
        return result
    finally:
        lock_fh.close()  # releases the flock


def daily_backup(backup_dir: str | Path | None = None) -> Path | None:
    """Keep seven daily dumps without pruning monthly or manual recovery files."""
    root = _ensure_private_dir(Path(backup_dir or settings.backup_dir))
    stamp = root / ".last_daily_backup"

    def recent() -> bool:
        try:
            return datetime.now() - datetime.fromisoformat(stamp.read_text().strip()) < timedelta(hours=20)
        except (OSError, ValueError):
            return False

    if recent():
        return None
    with open(root / ".backup.lock", "w") as lock_fh:
        if fcntl is not None:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return None
        if recent():
            return None
        result = backup_database("daily", backup_dir=root)
        if result:
            stamp.write_text(datetime.now().isoformat())
            prune_old_backups(root, keep=settings.daily_backup_keep, reason="daily")
        return result


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

    When ``settings.pg_docker_container`` is set, the command is wrapped in
    ``docker exec`` so it runs inside the PostgreSQL container (where the
    client binaries live). Input/output is streamed over stdin/stdout, so the
    dump file always lands on the host filesystem regardless of where the
    binary runs. Otherwise the command runs natively and ``PGPASSWORD`` is
    passed through the environment.
    """
    container = settings.pg_docker_container
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


def backup_database(
    reason: str,
    backup_dir: str | Path | None = None,
    *,
    database_url: str | None = None,
    offsite: bool = True,
) -> Path | None:
    """Create a database backup using ``pg_dump`` and return its path.

    The dump file is created 0600 inside a 0700 directory, and is checked
    for a pg_dump header before being reported as a success. When
    ``offsite`` and BACKUP_OFFSITE_COMMAND is set, the hook runs afterwards.
    """
    root = _ensure_private_dir(Path(backup_dir or settings.backup_dir))

    safe_reason = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in reason
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    parsed = urlparse(database_url or DATABASE_URL)
    db_name = parsed.path.lstrip("/") or "rpa"
    backup_path = root / f"{db_name}-{timestamp}-{safe_reason}.sql"

    # Dump to stdout (no -f) so the file is written on the host even when the
    # command runs inside the database container.
    # Include GRANTs: reporting views and the restricted runtime role must
    # remain usable after a restore on the same PostgreSQL cluster.
    pg_args = ["pg_dump", "--clean", "--if-exists", "--no-owner"]
    pg_args.extend(_pg_connection_args(parsed))
    pg_args.append(db_name)
    argv, env = _wrap_for_docker(pg_args, parsed, interactive=False)

    try:
        # 0600 from creation (not chmod after): the dump holds every customer
        # row, so it must never exist world-readable, even briefly.
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # O_CREAT mode is ignored for a pre-existing file
        with os.fdopen(fd, "wb") as fh:
            subprocess.run(
                argv, env=env, check=True, stdout=fh, stderr=subprocess.PIPE, text=True
            )
    except FileNotFoundError as exc:
        backup_path.unlink(missing_ok=True)
        logger.error(
            "pg_dump/docker not found; set PG_DOCKER_CONTAINER or install "
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

    problem = validate_dump(backup_path)
    if problem:
        logger.error("pg_dump produced an invalid backup: %s", problem)
        backup_path.unlink(missing_ok=True)
        return None
    if offsite:
        run_offsite_hook(backup_path)
    return backup_path


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
            "psql/docker not found; set PG_DOCKER_CONTAINER or install "
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


# ── Restore drill ───────────────────────────────────────────────────────────

# Tables whose row counts are printed by the drill (missing ones are reported).
DRILL_TABLES = (
    "alembic_version", "user", "customers", "orders", "upload_batches",
    "media_posts", "xhs_posts", "zhihu_posts", "pgy_notes", "collector_runs",
    "weekly_report_runs",
)


def _sync_dsn(url: str, db_name: str | None = None) -> str:
    dsn = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    if db_name is not None:
        parsed = urlparse(dsn)
        dsn = parsed._replace(path=f"/{db_name}").geturl()
    return dsn


def restore_drill(
    dump_path: str | Path | None = None,
    *,
    backup_dir: str | Path | None = None,
    database_url: str | None = None,
    out=print,
) -> bool:
    """Restore a dump into a throwaway database, print sanity counts, drop it.

    Proves the backups are actually restorable without touching the live
    database: a uniquely named ``rpa_restore_drill_<timestamp>`` database is
    created on the same server, the dump is loaded with ``psql -v
    ON_ERROR_STOP=1``, row counts of key tables are printed, and the
    database is always dropped afterwards. Needs CREATEDB on the role (run
    with the migration/owner URL). Returns True when the restore succeeded
    and alembic_version is present.
    """
    import psycopg2
    from psycopg2 import sql

    url = database_url or DATABASE_URL
    dump = Path(dump_path) if dump_path else latest_dump(backup_dir or settings.backup_dir)
    if dump is None:
        out("restore drill: no backup file found")
        return False
    problem = validate_dump(dump)
    if problem:
        out(f"restore drill: {problem}")
        return False

    drill_db = f"rpa_restore_drill_{datetime.now():%Y%m%d%H%M%S}"
    admin = psycopg2.connect(_sync_dsn(url, "postgres"))
    admin.autocommit = True
    ok = False
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(drill_db)))
        parsed = urlparse(url)
        pg_args = ["psql", "-q", "-v", "ON_ERROR_STOP=1"]
        pg_args.extend(_pg_connection_args(parsed))
        pg_args.append(drill_db)
        argv, env = _wrap_for_docker(pg_args, parsed, interactive=True)
        out(f"restore drill: restoring {dump.name} into {drill_db} ...")
        with open(dump, "rb") as fh:
            result = subprocess.run(argv, env=env, stdin=fh, capture_output=True)
        if result.returncode != 0:
            out(f"restore drill: psql failed (exit {result.returncode}): "
                f"{result.stderr.decode(errors='replace')[-2000:]}")
            return False
        conn = psycopg2.connect(_sync_dsn(url, drill_db))
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
                )
                out(f"restore drill: {cur.fetchone()[0]} tables restored")
                for table in DRILL_TABLES:
                    cur.execute("SELECT to_regclass(%s)", (f'public."{table}"',))
                    if cur.fetchone()[0] is None:
                        out(f"  {table}: MISSING")
                        continue
                    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                    out(f"  {table}: {cur.fetchone()[0]} rows")
                cur.execute("SELECT to_regclass('public.alembic_version')")
                if cur.fetchone()[0] is not None:
                    cur.execute("SELECT version_num FROM alembic_version")
                    versions = [r[0] for r in cur.fetchall()]
                    out(f"restore drill: alembic revision {', '.join(versions) or '(none)'}")
                    ok = bool(versions)
                else:
                    out("restore drill: alembic_version missing — not a full application dump")
        finally:
            conn.close()
        return ok
    finally:
        try:
            with admin.cursor() as cur:
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(drill_db)))
            out(f"restore drill: dropped {drill_db}")
        finally:
            admin.close()
        out(f"restore drill: {'OK' if ok else 'FAILED'}")


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli_database_url() -> str:
    # Same precedence as alembic/env.py: the root-only migration URL wins.
    return os.environ.get("RAP_MIGRATION_DATABASE_URL") or DATABASE_URL


def main(argv: list[str] | None = None) -> int:
    from ..utils.logging_setup import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(prog="python -m app.db.backup")
    sub = parser.add_subparsers(dest="command", required=True)
    p_backup = sub.add_parser("backup", help="take one pg_dump now")
    p_backup.add_argument("reason", help="label in the file name, e.g. pre-migrate")
    p_backup.add_argument("--backup-dir", default=None)
    p_backup.add_argument("--no-offsite", action="store_true", help="skip BACKUP_OFFSITE_COMMAND")
    p_backup.add_argument("--keep", type=int, default=None,
                          help="after success, keep only the newest N dumps with this reason")
    p_verify = sub.add_parser("verify", help="check a dump file's size and pg_dump header")
    p_verify.add_argument("path")
    p_drill = sub.add_parser("restore-drill", help="restore the latest dump into a temp DB, count, drop")
    p_drill.add_argument("--dump", default=None)
    p_drill.add_argument("--backup-dir", default=None)
    args = parser.parse_args(argv)

    if args.command == "backup":
        path = backup_database(
            args.reason, backup_dir=args.backup_dir,
            database_url=_cli_database_url(), offsite=not args.no_offsite,
        )
        if path is None:
            print("backup FAILED", file=sys.stderr)
            return 1
        if args.keep is not None:
            safe_reason = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in args.reason)
            prune_old_backups(path.parent, keep=args.keep, reason=safe_reason)
        print(path)
        return 0
    if args.command == "verify":
        problem = validate_dump(args.path)
        print(problem or "OK")
        return 1 if problem else 0
    ok = restore_drill(args.dump, backup_dir=args.backup_dir, database_url=_cli_database_url())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

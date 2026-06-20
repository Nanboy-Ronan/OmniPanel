from __future__ import annotations

import shutil
import subprocess

import psycopg2
import pytest

from app.db import backup


def test_monthly_backup_runs_once_and_records_timestamp(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        kwargs["stdout"].write(b"-- backup sql")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.delenv("RPA_PG_DOCKER_CONTAINER", raising=False)
    monkeypatch.setattr(
        backup,
        "DATABASE_URL",
        "postgresql+asyncpg://rpa:secret@127.0.0.1:55432/rpa",
    )
    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    first = backup.monthly_backup(tmp_path)
    second = backup.monthly_backup(tmp_path)

    assert first is not None
    assert first.exists()
    assert second is None
    assert (tmp_path / ".last_monthly_backup").exists()
    assert len(calls) == 1

    args, kwargs = calls[0]
    # Dump streams to stdout (no -f) so the file lands on the host.
    assert args[:5] == ["pg_dump", "--clean", "--if-exists", "--no-owner", "--no-acl"]
    assert "-f" not in args
    assert ["-h", "127.0.0.1"] == args[args.index("-h") : args.index("-h") + 2]
    assert ["-p", "55432"] == args[args.index("-p") : args.index("-p") + 2]
    assert ["-U", "rpa"] == args[args.index("-U") : args.index("-U") + 2]
    assert args[-1] == "rpa"
    assert kwargs["env"]["PGPASSWORD"] == "secret"
    assert kwargs["check"] is True
    assert kwargs["stdout"] is not None


def test_backup_database_routes_through_docker_when_configured(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        kwargs["stdout"].write(b"-- backup sql")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setenv("RPA_PG_DOCKER_CONTAINER", "rpa-postgres")
    monkeypatch.setattr(
        backup,
        "DATABASE_URL",
        "postgresql+asyncpg://rpa:secret@127.0.0.1:5432/rpa",
    )
    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    path = backup.backup_database("manual", backup_dir=tmp_path)
    assert path is not None and path.exists()

    args, kwargs = calls[0]
    # Command is wrapped in `docker exec`, with the password injected via -e and
    # NOT leaked into the host process environment.
    assert args[:2] == ["docker", "exec"]
    assert "-e" in args and "PGPASSWORD=secret" in args
    assert "rpa-postgres" in args
    assert args[args.index("rpa-postgres") + 1] == "pg_dump"
    assert "PGPASSWORD" not in kwargs["env"]


def test_restore_database_uses_psql_with_error_stop(monkeypatch, tmp_path):
    calls = []
    backup_path = tmp_path / "rpa.sql"
    backup_path.write_text("-- backup sql", encoding="utf-8")

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.delenv("RPA_PG_DOCKER_CONTAINER", raising=False)
    monkeypatch.setattr(
        backup,
        "DATABASE_URL",
        "postgresql+asyncpg://rpa:secret@127.0.0.1:55432/rpa",
    )
    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    assert backup.restore_database(backup_path) is True

    args, kwargs = calls[0]
    # Restore streams the dump via stdin (no -f).
    assert args[:3] == ["psql", "-v", "ON_ERROR_STOP=1"]
    assert "-f" not in args
    assert ["-h", "127.0.0.1"] == args[args.index("-h") : args.index("-h") + 2]
    assert ["-p", "55432"] == args[args.index("-p") : args.index("-p") + 2]
    assert ["-U", "rpa"] == args[args.index("-U") : args.index("-U") + 2]
    assert args[-1] == "rpa"
    assert kwargs["env"]["PGPASSWORD"] == "secret"
    assert kwargs["check"] is True
    assert kwargs["stdin"] is not None


def test_restore_database_returns_false_for_missing_file(tmp_path):
    assert backup.restore_database(tmp_path / "missing.sql") is False


def test_monthly_backup_skips_when_lock_held(monkeypatch, tmp_path):
    """A second worker must skip the backup while another holds the lock,
    instead of writing a second pg_dump into the same file (corruption)."""
    import fcntl

    ran = []

    def fake_backup_database(reason, backup_dir=None):
        ran.append(reason)
        return tmp_path / "should-not-happen.sql"

    monkeypatch.setattr(backup, "backup_database", fake_backup_database)

    # Simulate the first worker already holding the backup lock.
    held = open(tmp_path / ".backup.lock", "w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = backup.monthly_backup(tmp_path)
    finally:
        held.close()

    assert result is None
    assert ran == []  # pg_dump never invoked while the lock was held


def test_prune_old_backups_removes_oldest_beyond_keep(tmp_path):
    """Files beyond the keep limit should be deleted, oldest first."""
    import time

    files = []
    for i in range(7):
        f = tmp_path / f"rpa-2026050{i}-monthly.sql"
        f.write_text(f"-- backup {i}")
        time.sleep(0.01)  # ensure distinct mtimes
        files.append(f)

    removed = backup.prune_old_backups(tmp_path, keep=5)
    assert removed == 2
    # The two oldest (index 0 and 1) should be gone.
    assert not files[0].exists()
    assert not files[1].exists()
    for f in files[2:]:
        assert f.exists()


def test_prune_old_backups_keeps_all_when_under_limit(tmp_path):
    for i in range(3):
        (tmp_path / f"rpa-backup-{i}.sql").write_text("-- sql")

    removed = backup.prune_old_backups(tmp_path, keep=5)
    assert removed == 0
    assert len(list(tmp_path.iterdir())) == 3


def test_prune_old_backups_ignores_dotfiles(tmp_path):
    (tmp_path / ".last_monthly_backup").write_text("2026-01-01")
    (tmp_path / ".backup.lock").write_text("")
    for i in range(2):
        (tmp_path / f"rpa-backup-{i}.sql").write_text("-- sql")

    removed = backup.prune_old_backups(tmp_path, keep=1)
    assert removed == 1
    assert (tmp_path / ".last_monthly_backup").exists()
    assert (tmp_path / ".backup.lock").exists()


def test_prune_called_after_successful_monthly_backup(monkeypatch, tmp_path):
    """prune_old_backups should be invoked after a successful monthly backup."""
    prune_calls = []

    def fake_run(args, **kwargs):
        kwargs["stdout"].write(b"-- backup sql")
        return subprocess.CompletedProcess(args, 0)

    def fake_prune(root, keep=None):
        prune_calls.append(root)
        return 0

    monkeypatch.delenv("RPA_PG_DOCKER_CONTAINER", raising=False)
    monkeypatch.setattr(backup, "DATABASE_URL", "postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa")
    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    monkeypatch.setattr(backup, "prune_old_backups", fake_prune)

    result = backup.monthly_backup(tmp_path)
    assert result is not None
    assert len(prune_calls) == 1


def test_monthly_backup_can_restore_polluted_database(
    monkeypatch, tmp_path, pg_sync_url, pg_async_url
):
    if not shutil.which("pg_dump") or not shutil.which("psql"):
        pytest.fail("pg_dump and psql are required for backup restore integration test")

    monkeypatch.setattr(backup, "DATABASE_URL", pg_async_url)

    conn = psycopg2.connect(pg_sync_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE backup_restore_probe (id integer primary key, label text)")
        cur.execute("INSERT INTO backup_restore_probe VALUES (1, 'clean')")
    conn.close()

    backup_path = backup.monthly_backup(tmp_path)
    assert backup_path is not None
    assert backup_path.exists()
    assert (tmp_path / ".last_monthly_backup").exists()

    conn = psycopg2.connect(pg_sync_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE backup_restore_probe SET label = 'polluted' WHERE id = 1")
        cur.execute("INSERT INTO backup_restore_probe VALUES (2, 'extra')")
    conn.close()

    assert backup.restore_database(backup_path) is True

    conn = psycopg2.connect(pg_sync_url)
    with conn.cursor() as cur:
        cur.execute("SELECT id, label FROM backup_restore_probe ORDER BY id")
        rows = cur.fetchall()
    conn.close()

    assert rows == [(1, "clean")]

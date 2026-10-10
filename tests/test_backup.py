from __future__ import annotations

import shutil
import subprocess

import psycopg2
import pytest

from app.db import backup

# What a real plain-format pg_dump starts with; backup_database rejects
# output without this header.
_FAKE_DUMP = b"--\n-- PostgreSQL database dump\n--\n-- backup sql\n"


def test_monthly_backup_runs_once_and_records_timestamp(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        kwargs["stdout"].write(_FAKE_DUMP)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.settings, "pg_docker_container", None)
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
    assert args[:4] == ["pg_dump", "--clean", "--if-exists", "--no-owner"]
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
        kwargs["stdout"].write(_FAKE_DUMP)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.settings, "pg_docker_container", "rpa-postgres")
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

    monkeypatch.setattr(backup.settings, "pg_docker_container", None)
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
        kwargs["stdout"].write(_FAKE_DUMP)
        return subprocess.CompletedProcess(args, 0)

    def fake_prune(root, keep=None, reason=None):
        prune_calls.append(root)
        return 0

    monkeypatch.setattr(backup.settings, "pg_docker_container", None)
    monkeypatch.setattr(backup, "DATABASE_URL", "postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa")
    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    monkeypatch.setattr(backup, "prune_old_backups", fake_prune)

    result = backup.monthly_backup(tmp_path)
    assert result is not None
    assert len(prune_calls) == 1


def test_daily_backup_keeps_manual_and_monthly_files(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        kwargs["stdout"].write(_FAKE_DUMP)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.settings, "pg_docker_container", None)
    monkeypatch.setattr(backup.settings, "daily_backup_keep", 1)
    monkeypatch.setattr(backup, "DATABASE_URL", "postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa")
    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    manual = tmp_path / "rpa-20260101-000000-before-clear-db.sql"
    monthly = tmp_path / "rpa-20260101-000000-monthly.sql"
    old_daily = tmp_path / "rpa-20260101-000000-daily.sql"
    for file in (manual, monthly, old_daily):
        file.write_text("-- old")
    assert backup.daily_backup(tmp_path)
    assert manual.exists() and monthly.exists() and not old_daily.exists()
    assert backup.daily_backup(tmp_path) is None


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


# ── permissions, validation, off-site hook, restore drill ───────────────────

def _fake_pg_dump(monkeypatch, payload=_FAKE_DUMP):
    def fake_run(args, **kwargs):
        if args and args[0] == "pg_dump":
            kwargs["stdout"].write(payload)
            return subprocess.CompletedProcess(args, 0)
        raise AssertionError(f"unexpected command {args}")

    monkeypatch.setattr(backup.settings, "pg_docker_container", None)
    monkeypatch.setattr(backup.settings, "backup_offsite_command", None)
    monkeypatch.setattr(backup, "DATABASE_URL", "postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa")
    monkeypatch.setattr(backup.subprocess, "run", fake_run)


def test_backup_file_is_0600_and_dir_is_0700(monkeypatch, tmp_path):
    import os
    import stat

    _fake_pg_dump(monkeypatch)
    old_umask = os.umask(0o022)  # a typical permissive umask
    try:
        target = tmp_path / "backups"
        target.mkdir(mode=0o755)
        path = backup.backup_database("manual", backup_dir=target)
    finally:
        os.umask(old_umask)
    assert path is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_invalid_dump_output_is_rejected_and_removed(monkeypatch, tmp_path):
    _fake_pg_dump(monkeypatch, payload=b"")
    assert backup.backup_database("manual", backup_dir=tmp_path) is None
    assert list(tmp_path.glob("*.sql")) == []


def test_validate_dump(tmp_path):
    good = tmp_path / "good.sql"
    good.write_bytes(_FAKE_DUMP)
    empty = tmp_path / "empty.sql"
    empty.write_bytes(b"")
    junk = tmp_path / "junk.sql"
    junk.write_bytes(b"<html>proxy error</html>")
    assert backup.validate_dump(good) is None
    assert "空文件" in backup.validate_dump(empty)
    assert "文件头" in backup.validate_dump(junk)
    assert "无法读取" in backup.validate_dump(tmp_path / "missing.sql")


def test_offsite_hook_runs_with_dump_path(monkeypatch, tmp_path):
    marker = tmp_path / "offsite.txt"
    script = tmp_path / "copy.sh"
    script.write_text(f'#!/bin/sh\necho "$1" > "{marker}"\n')
    script.chmod(0o700)
    monkeypatch.setattr(backup.settings, "backup_offsite_command", f"{script}")
    dump = tmp_path / "rpa-x.sql"
    dump.write_bytes(_FAKE_DUMP)
    assert backup.run_offsite_hook(dump) is True
    assert marker.read_text().strip() == str(dump)


def test_offsite_hook_failure_alerts_but_keeps_backup(monkeypatch, tmp_path):
    from app.utils import wecom_bot

    alerts = []
    monkeypatch.setattr(wecom_bot, "send_wecom_alert", lambda text: alerts.append(text) or True)
    monkeypatch.setattr(backup.settings, "backup_offsite_command", "sh -c 'echo nope >&2; exit 3' --")
    dump = tmp_path / "rpa-y.sql"
    dump.write_bytes(_FAKE_DUMP)
    assert backup.run_offsite_hook(dump) is False
    assert dump.exists()
    assert len(alerts) == 1 and "异地备份" in alerts[0] and "退出码 3" in alerts[0]


def test_offsite_hook_timeout_alerts(monkeypatch, tmp_path):
    from app.utils import wecom_bot

    alerts = []
    monkeypatch.setattr(wecom_bot, "send_wecom_alert", lambda text: alerts.append(text) or True)
    monkeypatch.setattr(backup.settings, "backup_offsite_command", "sh -c 'sleep 5' --")
    monkeypatch.setattr(backup.settings, "backup_offsite_timeout_seconds", 1)
    dump = tmp_path / "rpa-z.sql"
    dump.write_bytes(_FAKE_DUMP)
    assert backup.run_offsite_hook(dump) is False
    assert "超过 1 秒" in alerts[0]


def test_offsite_hook_unset_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(backup.settings, "backup_offsite_command", None)
    assert backup.run_offsite_hook(tmp_path / "whatever.sql") is True


def test_cli_backup_returns_nonzero_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(backup, "backup_database", lambda *a, **kw: None)
    assert backup.main(["backup", "pre-migrate", "--backup-dir", str(tmp_path), "--no-offsite"]) == 1
    calls = {}

    def ok(reason, **kw):
        calls.update(kw, reason=reason)
        return tmp_path / "x.sql"

    monkeypatch.setattr(backup, "backup_database", ok)
    monkeypatch.setenv("RAP_MIGRATION_DATABASE_URL", "postgresql://owner:pw@127.0.0.1:5432/rpa")
    assert backup.main(["backup", "pre-migrate", "--backup-dir", str(tmp_path), "--no-offsite"]) == 0
    assert calls["reason"] == "pre-migrate" and calls["offsite"] is False
    assert calls["database_url"] == "postgresql://owner:pw@127.0.0.1:5432/rpa"


def test_restore_drill_restores_into_temp_db_and_drops_it(monkeypatch, tmp_path, pg_sync_url, pg_async_url):
    if not shutil.which("pg_dump") or not shutil.which("psql"):
        pytest.fail("pg_dump and psql are required for the restore drill test")
    monkeypatch.setattr(backup.settings, "pg_docker_container", None)
    monkeypatch.setattr(backup.settings, "backup_offsite_command", None)
    monkeypatch.setattr(backup, "DATABASE_URL", pg_async_url)

    conn = psycopg2.connect(pg_sync_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)")
        cur.execute("DELETE FROM alembic_version")
        cur.execute("INSERT INTO alembic_version VALUES ('drill_test_rev')")
    conn.close()

    dump = backup.backup_database("drill", backup_dir=tmp_path)
    assert dump is not None

    lines: list[str] = []
    assert backup.restore_drill(backup_dir=tmp_path, database_url=pg_async_url, out=lines.append) is True
    text = "\n".join(lines)
    assert "drill_test_rev" in text and "orders: 0 rows" in text and "OK" in text

    conn = psycopg2.connect(pg_sync_url)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'rpa_restore_drill_%'")
        assert cur.fetchone()[0] == 0
        cur.execute("DROP TABLE alembic_version")
    conn.commit()
    conn.close()


def test_watchdog_flags_empty_latest_dump(tmp_path):
    import asyncio
    import dataclasses
    from datetime import datetime

    from app.scheduler import run_watchdog_checks

    @dataclasses.dataclass
    class S:
        collector_enabled: bool = False
        wechat_auto_sync_enabled: bool = False
        rap_disable_monthly_backup: bool = False
        watchdog_max_age_hours: int = 30
        watchdog_backup_max_age_days: int = 35
        watchdog_daily_backup_max_age_days: int = 2
        backup_dir: str = str(tmp_path)

    (tmp_path / ".last_monthly_backup").write_text(datetime.now().isoformat())
    (tmp_path / ".last_daily_backup").write_text(datetime.now().isoformat())
    (tmp_path / "rpa-20260101-000000-daily.sql").write_bytes(b"")
    problems = asyncio.run(run_watchdog_checks(S()))
    assert len(problems) == 1 and "空文件" in problems[0]


"""The sync (psycopg2) engine must use APP_TIMEZONE like the async engine,
and alembic 0020 converts the UTC-naive rows it wrote before the fix."""
from __future__ import annotations

import runpy
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/0020_sync_engine_tz_backfill.py"


def test_sync_engine_session_timezone_matches_app_timezone(pg_sync_url):
    import app.db as db

    assert db.sync_connect_args("Asia/Shanghai") == {"options": "-c timezone=Asia/Shanghai"}
    # The module-level engine is built with these args (not connected here:
    # its URL is the configured application database, which tests never use).
    import inspect
    assert "connect_args=sync_connect_args()" in inspect.getsource(db)

    engine = create_engine(pg_sync_url, connect_args=db.sync_connect_args("Asia/Shanghai"))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT current_setting('TimeZone')")).scalar_one() == "Asia/Shanghai"
            # A server-generated naive timestamp now lands in Shanghai wall time.
            conn.execute(text(
                "INSERT INTO collector_runs (platform, status, triggered_by) VALUES ('xhs', 'success', 'schedule')"
            ))
            drift = conn.execute(text(
                "SELECT abs(extract(epoch FROM started_at - (now() AT TIME ZONE 'Asia/Shanghai'))) "
                "FROM collector_runs"
            )).scalar_one()
            conn.rollback()
        assert drift < 5
    finally:
        engine.dispose()


def _run_migration(conn, fn_name: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with Operations.context(MigrationContext.configure(conn)):
        runpy.run_path(str(MIGRATION))[fn_name]()


@pytest.fixture
def utc_conn(pg_sync_url, monkeypatch):
    monkeypatch.delenv("TZ_BACKFILL_SOURCE_TIMEZONE", raising=False)
    monkeypatch.setenv("APP_TIMEZONE", "Asia/Shanghai")
    engine = create_engine(pg_sync_url)
    with engine.begin() as conn:
        conn.execute(text("SET TIME ZONE 'UTC'"))  # the server default the sync engine used
        yield conn
    engine.dispose()


def _seed(conn) -> None:
    conn.execute(text("INSERT INTO xhs_accounts (id, name) VALUES (901, 'tz-acc')"))
    conn.execute(text("INSERT INTO wx_channels_accounts (id, name) VALUES (902, 'tz-ch')"))
    conn.execute(text(
        "INSERT INTO collector_runs (id, platform, status, started_at, finished_at) "
        "VALUES (1, 'xhs', 'success', '2026-10-01 22:30:00', '2026-10-01 22:41:00'), "
        "       (2, 'xhs', 'running', '2026-10-02 22:30:00', NULL)"
    ))
    conn.execute(text(
        "INSERT INTO pgy_notes (account_id, note_id, publish_date, data_date, created_at, updated_at) "
        "VALUES (901, 'n1', '2026-09-30', '2026-10-01', '2026-10-01 01:00:00', '2026-10-02 23:30:00')"
    ))
    conn.execute(text(
        "INSERT INTO wx_channels_posts (account_id, video_id, title, publish_date, created_at, updated_at) "
        "VALUES (902, 'v1', 't', '2026-09-30', '2026-10-01 01:00:00', '2026-10-01 02:00:00')"
    ))
    conn.execute(text(
        "INSERT INTO xhs_account_daily_metrics (account_id, metric_date, created_at, updated_at) "
        "VALUES (901, '2026-10-01', '2026-10-01 16:00:00', '2026-10-01 16:00:00')"
    ))
    conn.execute(text(
        "INSERT INTO xhs_audience_source_daily (account_id, snapshot_date, window_label, source_type, created_at, updated_at) "
        "VALUES (901, '2026-10-01', 'seven', 1, '2026-10-01 16:00:00', '2026-10-01 16:00:00')"
    ))
    conn.execute(text(
        "INSERT INTO upload_batches (id, filename, platform, file_sha256, uploaded_at) "
        "VALUES (1, 'x.csv', 'youzan', 'h', '2026-10-01 10:00:00')"
    ))


def test_backfill_shifts_only_sync_engine_columns_and_downgrade_restores(utc_conn):
    conn = utc_conn
    _seed(conn)
    _run_migration(conn, "upgrade")

    q = lambda sql: conn.execute(text(sql)).one()  # noqa: E731
    assert q("SELECT started_at, finished_at FROM collector_runs WHERE id = 1") == (
        datetime(2026, 10, 2, 6, 30), datetime(2026, 10, 2, 6, 41),
    )
    assert q("SELECT started_at, finished_at FROM collector_runs WHERE id = 2") == (
        datetime(2026, 10, 3, 6, 30), None,
    )
    assert q("SELECT created_at, updated_at, publish_date, data_date FROM pgy_notes") == (
        datetime(2026, 10, 1, 9, 0), datetime(2026, 10, 3, 7, 30), date(2026, 9, 30), date(2026, 10, 1),
    )
    assert q("SELECT created_at, updated_at, publish_date FROM wx_channels_posts") == (
        datetime(2026, 10, 1, 9, 0), datetime(2026, 10, 1, 10, 0), date(2026, 9, 30),
    )
    assert q("SELECT created_at, metric_date FROM xhs_account_daily_metrics") == (
        datetime(2026, 10, 2, 0, 0), date(2026, 10, 1),
    )
    assert q("SELECT updated_at, snapshot_date FROM xhs_audience_source_daily") == (
        datetime(2026, 10, 2, 0, 0), date(2026, 10, 1),
    )
    # Mixed-writer table is excluded.
    assert q("SELECT uploaded_at FROM upload_batches") == (datetime(2026, 10, 1, 10, 0),)

    _run_migration(conn, "downgrade")
    assert q("SELECT started_at, finished_at FROM collector_runs WHERE id = 1") == (
        datetime(2026, 10, 1, 22, 30), datetime(2026, 10, 1, 22, 41),
    )
    assert q("SELECT created_at, updated_at FROM pgy_notes") == (
        datetime(2026, 10, 1, 1, 0), datetime(2026, 10, 2, 23, 30),
    )
    assert q("SELECT created_at FROM xhs_account_daily_metrics") == (datetime(2026, 10, 1, 16, 0),)


def test_backfill_is_noop_when_server_already_uses_app_timezone(utc_conn):
    conn = utc_conn
    _seed(conn)
    conn.execute(text("SET TIME ZONE 'Asia/Shanghai'"))
    _run_migration(conn, "upgrade")
    assert conn.execute(text("SELECT started_at FROM collector_runs WHERE id = 1")).scalar_one() == (
        datetime(2026, 10, 1, 22, 30)
    )

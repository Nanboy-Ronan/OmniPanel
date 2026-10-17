"""Upgrade an existing 0022 deployment without losing its users."""
import uuid

import psycopg2

from test_alembic_sanity import fresh_pg_db, _alembic, _table_names


def test_upgrade_existing_platform_preserves_users(fresh_pg_db):
    async_url, sync_url, _ = fresh_pg_db
    result = _alembic(["upgrade", "0022_order_status"], async_url)
    assert result.returncode == 0, result.stderr
    assert "integration_tokens" not in _table_names(sync_url)
    user_id = str(uuid.uuid4())
    with psycopg2.connect(sync_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO "user" (id,email,hashed_password,is_active,is_superuser,is_verified,role) VALUES (%s,%s,%s,true,false,true,%s)',
                           (user_id, "migration@example.com", "synthetic", "analyst"))
    result = _alembic(["upgrade", "0023_integration_tokens"], async_url)
    assert result.returncode == 0, result.stderr
    with psycopg2.connect(sync_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO weekly_report_runs (week_start,week_end,status,wecom_sent,html_content) VALUES ('2026-09-07','2026-09-13','success',true,'saved report')")
    result = _alembic(["upgrade", "head"], async_url)
    assert result.returncode == 0, result.stderr
    assert "integration_tokens" in _table_names(sync_url)
    assert "workbuddy_report_jobs" in _table_names(sync_url)
    with psycopg2.connect(sync_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT html_content, wecom_sent FROM weekly_report_runs")
            assert cursor.fetchone() == ("saved report", True)
            cursor.execute('SELECT email FROM "user" WHERE id=%s', (user_id,))
            assert cursor.fetchone()[0] == "migration@example.com"
            cursor.execute("INSERT INTO integration_tokens (id,user_id,name,token_hash,scopes,expires_at) VALUES (%s,%s,%s,%s,%s,now()+interval '1 day')",
                           (str(uuid.uuid4()), user_id, "test", "a" * 64, '["metadata:read"]'))
            cursor.execute('DELETE FROM "user" WHERE id=%s', (user_id,))
            cursor.execute("SELECT count(*) FROM integration_tokens")
            assert cursor.fetchone()[0] == 0
    result = _alembic(["check"], async_url)
    assert result.returncode == 0, result.stderr


def _columns(sync_url, table):
    with psycopg2.connect(sync_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,))
            return {row[0] for row in cursor.fetchall()}


def test_hardening_columns_round_trip_and_keep_rows(fresh_pg_db):
    async_url, sync_url, _ = fresh_pg_db
    assert _alembic(["upgrade", "0026_collector_repair"], async_url).returncode == 0
    with psycopg2.connect(sync_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO collector_repairs (id,secret_hash,expires_at) VALUES ('r1',%s,now())", ("a" * 64,))
    result = _alembic(["upgrade", "head"], async_url)
    assert result.returncode == 0, result.stderr
    assert {"issued_token_id", "created_at"} <= _columns(sync_url, "workbuddy_oauth_requests")
    assert {"created_at", "user_code"} <= _columns(sync_url, "collector_repairs")
    assert "prepare_attempts" in _columns(sync_url, "workbuddy_report_jobs")
    with psycopg2.connect(sync_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT created_at IS NOT NULL FROM collector_repairs WHERE id='r1'")
            assert cursor.fetchone()[0] is True
    result = _alembic(["downgrade", "0026_collector_repair"], async_url)
    assert result.returncode == 0, result.stderr
    assert "user_code" not in _columns(sync_url, "collector_repairs")
    assert "issued_token_id" not in _columns(sync_url, "workbuddy_oauth_requests")
    assert _alembic(["upgrade", "head"], async_url).returncode == 0
    assert _alembic(["check"], async_url).returncode == 0


def test_collector_requests_round_trip_and_single_open_request(fresh_pg_db):
    async_url, sync_url, _ = fresh_pg_db
    result = _alembic(["upgrade", "head"], async_url)
    assert result.returncode == 0, result.stderr
    assert {"portal_name", "pgy_portal_name"} <= _columns(sync_url, "xhs_accounts")
    assert "portal_name" in _columns(sync_url, "wx_channels_accounts")
    with psycopg2.connect(sync_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO collector_requests (platform, account_id, reason) VALUES ('xhs', 1, 'repair')")
            cursor.execute("INSERT INTO collector_requests (platform, account_id, reason, status) VALUES ('xhs', 1, 'repair', 'done')")
            cursor.execute("INSERT INTO collector_requests (platform, account_id, reason) VALUES ('zhihu', NULL, 'admin')")
            try:
                cursor.execute("INSERT INTO collector_requests (platform, account_id, reason) VALUES ('zhihu', NULL, 'admin')")
                raise AssertionError("second open request for the same target was accepted")
            except psycopg2.errors.UniqueViolation:
                pass
    result = _alembic(["downgrade", "0027_workbuddy_hardening"], async_url)
    assert result.returncode == 0, result.stderr
    assert "collector_requests" not in _table_names(sync_url)
    assert "portal_name" not in _columns(sync_url, "xhs_accounts")
    assert _alembic(["upgrade", "head"], async_url).returncode == 0
    check = _alembic(["check"], async_url)
    assert check.returncode == 0, check.stderr + check.stdout

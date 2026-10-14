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

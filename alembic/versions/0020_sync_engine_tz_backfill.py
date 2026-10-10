"""Convert naive timestamps written by the sync engine from server time to APP_TIMEZONE.

Background
----------
The async engine (asyncpg) has always set the session ``timezone`` to
APP_TIMEZONE (Asia/Shanghai), but the sync engine (psycopg2, used by the
collector bookkeeping and the media-export ETLs) did not, so it ran in the
server default (UTC on the VM). Every *server-generated* value (``now()`` /
``server_default=func.now()``) it stored into a ``timestamp without time
zone`` column is therefore UTC wall time, while the rest of the database is
Shanghai wall time. The engine fix ships in the same commit
(app/db/__init__.py: ``connect_args={"options": "-c timezone=..."}``); this
revision converts the rows already written.

Conversion: ``(col AT TIME ZONE <source tz>) AT TIME ZONE <app tz>``, where
the source tz is this migration connection's session TimeZone (the same
server/database default the sync engine used; override with
TZ_BACKFILL_SOURCE_TIMEZONE) and the app tz is APP_TIMEZONE (default
Asia/Shanghai). For UTC -> Asia/Shanghai that is +8h (no DST). When both are
the same the revision is a no-op. All existing rows of the included columns
are converted: every one of them was written through the unfixed sync
engine.

Included (naive, server-generated, written ONLY through the sync engine —
verified by grepping every writer; each table was created after the ETL
moved to the sync engine on 2026-05-29, so no row predates it):
  * collector_runs.started_at, collector_runs.finished_at
      app/collector/runs.py start_run/finish_run (SyncSessionLocal) are the
      only writers; finish_run sets finished_at = now(). The watchdog reaper
      (app/scheduler.py) also uses the sync engine.
  * pgy_notes.created_at, pgy_notes.updated_at
      only app/db/etl/pgy.py upsert via SyncSessionLocal (app/views/media/pgy.py).
  * wx_channels_posts.created_at, wx_channels_posts.updated_at
      only app/db/etl/channels.py upsert via SyncSessionLocal.
  * xhs_account_daily_metrics.created_at, .updated_at
  * xhs_audience_source_daily.created_at, .updated_at
      only app/db/etl/xhs_overview.py upserts via SyncSessionLocal.

Excluded:
  * Platform-supplied dates (publish_date, data_date, metric_date,
    snapshot_date, order_date, ...): ``date`` columns from the exports, never
    server-generated.
  * xhs_posts / zhihu_posts / xhs_accounts / saved_query timestamps:
    ``timestamp with time zone`` — stored as absolute instants, unaffected by
    the session timezone.
  * youzan_orders / jd_orders / tmall_orders.created_at and
    upload_rejected_rows.created_at: written by the order ETL, which ran
    through the async engine (session.run_sync) until 2026-05-29 and through
    the sync engine afterwards — mixed history, no reliable boundary.
  * upload_batches.uploaded_at: rows are inserted by the async upload
    endpoint (Shanghai) and, on the legacy path, by ingest_upload (sync) —
    mixed writers.
  * Every other naive timestamp (media_*, operation_log, weekly_report_runs,
    wx_channels_accounts, ...): written through the async engine, already
    in APP_TIMEZONE.

Deploy note: alembic runs before the backend restarts, so a collector run
recorded in the seconds between this migration and the restart could still
be UTC. Deploy outside the collector window (06:30) and verify-all window.

Downgrade converts the same columns back (APP_TIMEZONE -> source tz) for
every row, including rows written after the upgrade, which matches the
pre-fix engine's convention the downgraded code expects.
"""

import os

from alembic import op
import sqlalchemy as sa

revision = "0020_sync_engine_tz_backfill"
down_revision = "0019_upload_recovery_attempts"
branch_labels = None
depends_on = None

SYNC_ENGINE_COLUMNS = {
    "collector_runs": ("started_at", "finished_at"),
    "pgy_notes": ("created_at", "updated_at"),
    "wx_channels_posts": ("created_at", "updated_at"),
    "xhs_account_daily_metrics": ("created_at", "updated_at"),
    "xhs_audience_source_daily": ("created_at", "updated_at"),
}


def _zones() -> tuple[str, str]:
    bind = op.get_bind()
    source = os.environ.get("TZ_BACKFILL_SOURCE_TIMEZONE") or bind.execute(
        sa.text("SELECT current_setting('TimeZone')")
    ).scalar_one()
    from app.config import Settings  # same resolution (.env / env) as the app

    return source, Settings().app_timezone


def _convert(from_tz: str, to_tz: str) -> None:
    bind = op.get_bind()
    same = bind.execute(
        sa.text(
            "SELECT (TIMESTAMP '2026-01-01 00:00' AT TIME ZONE :a) "
            "= (TIMESTAMP '2026-01-01 00:00' AT TIME ZONE :b) "
            "AND (TIMESTAMP '2026-07-01 00:00' AT TIME ZONE :a) "
            "= (TIMESTAMP '2026-07-01 00:00' AT TIME ZONE :b)"
        ),
        {"a": from_tz, "b": to_tz},
    ).scalar_one()
    if same:
        return
    for table, columns in SYNC_ENGINE_COLUMNS.items():
        assignments = ", ".join(
            f"{col} = ({col} AT TIME ZONE :from_tz) AT TIME ZONE :to_tz" for col in columns
        )
        bind.execute(
            sa.text(f"UPDATE {table} SET {assignments}"),
            {"from_tz": from_tz, "to_tz": to_tz},
        )


def upgrade() -> None:
    source, target = _zones()
    _convert(source, target)


def downgrade() -> None:
    source, target = _zones()
    _convert(target, source)

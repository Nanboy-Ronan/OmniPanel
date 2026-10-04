"""Give ad hoc analytics a restricted database role and masked views."""

from alembic import op
from sqlalchemy import inspect, text

revision = "0017_reporting_role"
down_revision = "0016_weekly_notification_text"
branch_labels = None
depends_on = None

ROLE = "rpa_analytics_readonly"
APP_ROLE = "rpa_app"
TABLES = (
    "orders", "upload_batches", "media_accounts", "media_posts",
    "media_post_metrics_daily", "media_article_traffic", "media_sync_runs",
    "collector_runs", "xhs_accounts", "xhs_posts", "xhs_account_daily_metrics",
    "xhs_audience_source_daily", "zhihu_posts", "wx_channels_accounts",
    "wx_channels_posts", "pgy_notes", "weekly_report_runs",
)
EXCLUDED = {
    "orders": {"customer_key", "receiver", "receiver_phone", "full_address", "buyer_nick"},
    "media_accounts": {"app_secret"},
    "xhs_accounts": {"cookies", "session_data"},
    "wx_channels_accounts": {"cookies", "session_data"},
    "upload_batches": {"uploaded_by"},
}
SENSITIVE_COLUMN_NAMES = {"app_secret", "raw_payload", "error_message", "cookies", "session_data"}


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rpa_analytics_readonly') THEN
            CREATE ROLE rpa_analytics_readonly NOLOGIN NOINHERIT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rpa_app') THEN
            CREATE ROLE rpa_app NOLOGIN;
        END IF;
    END $$"""))
    current_user = bind.execute(text("SELECT current_user")).scalar_one()
    bind.execute(text(f"GRANT {ROLE} TO {_quote(current_user)}"))
    bind.execute(text(f"GRANT {ROLE} TO {APP_ROLE}"))
    bind.execute(text(f"GRANT CONNECT ON DATABASE {_quote(bind.engine.url.database)} TO {APP_ROLE}"))
    bind.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
    bind.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
    bind.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    bind.execute(text(f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(current_user)} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO {APP_ROLE}"))
    bind.execute(text(f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(current_user)} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"))
    bind.execute(text("CREATE SCHEMA IF NOT EXISTS reporting"))
    bind.execute(text(f"GRANT USAGE ON SCHEMA reporting TO {ROLE}"))
    inspector = inspect(bind)
    for table in TABLES:
        columns = [
            column["name"] for column in inspector.get_columns(table, schema="public")
            if column["name"] not in EXCLUDED.get(table, set())
            and column["name"] not in SENSITIVE_COLUMN_NAMES
        ]
        if not columns:
            raise RuntimeError(f"Reporting view {table} has no safe columns")
        projection = ", ".join(_quote(column) for column in columns)
        name = _quote(table)
        bind.execute(text(f"CREATE OR REPLACE VIEW reporting.{name} AS SELECT {projection} FROM public.{name}"))
        bind.execute(text(f"GRANT SELECT ON reporting.{name} TO {ROLE}"))


def downgrade() -> None:
    # Keep the role because another database may use it. Remove this database's
    # views and privileges when rolling the migration back.
    op.execute("DROP SCHEMA reporting CASCADE")

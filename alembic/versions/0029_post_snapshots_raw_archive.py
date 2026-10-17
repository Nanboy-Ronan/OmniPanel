"""Append-only post metric snapshots and the raw upload archive.

The post tables are overwritten on every upload, so until now no history of a
post's metrics was kept. Each table gets a *_snapshots companion with one row
per post per capture day. The backfill seeds one snapshot per existing post
from its current values, dated by its last update: the only history that can
still be recovered.

``raw_upload_files`` records every imported file kept in the raw archive.
Both are exposed to the SQL console through ``reporting`` views.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0029_post_snapshots_raw_archive"
down_revision = "0028_collector_requests"
branch_labels = None
depends_on = None

ROLE = "rpa_analytics_readonly"
I, F, S = sa.Integer, sa.Float, lambda: sa.String(20)
# snapshot table -> (post table, metric columns, post updated_at is timestamptz)
SNAPSHOTS = {
    "xhs_post_snapshots": ("xhs_posts", [
        ("impressions", I), ("views", I), ("cover_click_rate", F), ("likes", I), ("comments", I),
        ("collects", I), ("new_followers", I), ("shares", I), ("avg_watch_time", F), ("danmu", I),
    ], True),
    "zhihu_post_snapshots": ("zhihu_posts", [
        ("reads", I), ("plays", I), ("likes", I), ("favorites", I), ("comments", I),
        ("collects", I), ("shares", I),
    ], True),
    "wx_channels_post_snapshots": ("wx_channels_posts", [
        ("plays", I), ("recommends", I), ("likes_thumb", I), ("comments", I), ("shares", I),
        ("new_fans", I), ("forwards_chat_moments", I), ("set_as_ringtone", I), ("set_as_status", I),
        ("set_as_moments_cover", I), ("wecom_link_clicks", I), ("wecom_link_click_users", I),
        ("added_to_contacts", I), ("added_to_contacts_users", I), ("avg_watch_duration", F),
        ("completion_rate", F),
    ], False),
    "pgy_note_snapshots": ("pgy_notes", [
        ("data_date", sa.Date), ("blogger_fans", I), ("blogger_quote", F), ("service_fee", F),
        ("impressions", I), ("reads", I), ("read_uv", I), ("play_rate_5s", S), ("read_rate_3s", S),
        ("avg_view_duration", F), ("video_completion_rate", S), ("interactions", I),
        ("interaction_rate", S), ("likes", I), ("collects", I), ("comments", I), ("shares", I),
        ("follows", I), ("organic_impressions", I), ("organic_reads", I), ("paid_impressions", I),
        ("paid_reads", I), ("boosted_impressions", I), ("boosted_reads", I), ("cost_per_read", F),
        ("cost_per_interaction", F),
    ], False),
}
RAW_FILE_VIEW_COLUMNS = ("id", "source", "account_id", "upload_batch_id", "filename", "sha256",
                         "size_bytes", "created_at", "purged_at")


def _app_timezone() -> str:
    from app.config import Settings  # same resolution (.env / env) as the app
    return Settings().app_timezone


def _has_reporting(bind) -> bool:
    return bool(bind.execute(text("SELECT to_regnamespace('reporting')")).scalar())


def upgrade():
    op.create_table(
        "raw_upload_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("account_id", sa.Integer()),
        sa.Column("upload_batch_id", sa.Integer(), sa.ForeignKey("upload_batches.id", ondelete="SET NULL")),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("archive_path", sa.String(1000)),
        sa.Column("uploaded_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_raw_upload_files_source_created", "raw_upload_files", ["source", "created_at"])
    op.create_index("ix_raw_upload_files_sha256", "raw_upload_files", ["sha256"])

    bind = op.get_bind()
    tz = _app_timezone()
    for table, (posts, metrics, aware) in SNAPSHOTS.items():
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("post_id", sa.Integer(), sa.ForeignKey(f"{posts}.id", ondelete="CASCADE"), nullable=False),
            sa.Column("captured_on", sa.Date(), nullable=False),
            sa.Column("source_file_id", sa.Integer(), sa.ForeignKey("raw_upload_files.id", ondelete="SET NULL")),
            *[sa.Column(name, kind()) for name, kind in metrics],
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("post_id", "captured_on", name=f"uq_{table}_post_day"),
        )
        op.create_index(f"ix_{table}_captured_on", table, ["captured_on"])
        names = ", ".join(name for name, _ in metrics)
        # Naive post timestamps are already app-timezone wall time (see 0020).
        day = "(updated_at AT TIME ZONE :tz)::date" if aware else "updated_at::date"
        bind.execute(text(
            f"INSERT INTO {table} (post_id, captured_on, {names}) SELECT id, {day}, {names} FROM {posts}"
        ), {"tz": tz} if aware else {})

    if _has_reporting(bind):
        for table in SNAPSHOTS:
            bind.execute(text(f"CREATE OR REPLACE VIEW reporting.{table} AS SELECT * FROM public.{table}"))
            bind.execute(text(f"GRANT SELECT ON reporting.{table} TO {ROLE}"))
        bind.execute(text(
            f"CREATE OR REPLACE VIEW reporting.raw_upload_files AS SELECT {', '.join(RAW_FILE_VIEW_COLUMNS)} "
            "FROM public.raw_upload_files"))
        bind.execute(text(f"GRANT SELECT ON reporting.raw_upload_files TO {ROLE}"))


def downgrade():
    bind = op.get_bind()
    if _has_reporting(bind):
        for table in [*SNAPSHOTS, "raw_upload_files"]:
            bind.execute(text(f"DROP VIEW IF EXISTS reporting.{table}"))
    for table in SNAPSHOTS:
        op.drop_table(table)
    op.drop_table("raw_upload_files")

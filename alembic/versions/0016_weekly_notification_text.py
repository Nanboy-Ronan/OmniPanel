"""Persist the weekly notification so delivery can retry without regenerating."""

from alembic import op
import sqlalchemy as sa

revision = "0016_weekly_notification_text"
down_revision = "0015_backfill_xhs_post_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("weekly_report_runs", sa.Column("notification_text", sa.Text(), nullable=True))
    # Older error alerts could set wecom_sent even though no report was delivered.
    op.execute("UPDATE weekly_report_runs SET wecom_sent = false WHERE status <> 'success'")


def downgrade() -> None:
    op.drop_column("weekly_report_runs", "notification_text")

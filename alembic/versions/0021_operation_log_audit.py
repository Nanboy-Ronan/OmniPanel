"""Let the operation log record sign-in attempts that match no account.

Failed or refused sign-ins (bad OAuth state, an unknown WeCom identity, a
locked-out password) are security events too, but there is no user row to
attribute them to. ``user_id`` becomes nullable for those rows, and the log
gains indexes for the time-range and action filters on the audit page.
"""

from alembic import op

revision = "0021_operation_log_audit"
down_revision = "0020_sync_engine_tz_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("operation_log", "user_id", nullable=True)
    op.create_index("ix_operation_log_timestamp", "operation_log", ["timestamp"])
    op.create_index("ix_operation_log_action", "operation_log", ["action"])


def downgrade() -> None:
    op.drop_index("ix_operation_log_action", table_name="operation_log")
    op.drop_index("ix_operation_log_timestamp", table_name="operation_log")
    op.execute("DELETE FROM operation_log WHERE user_id IS NULL")
    op.alter_column("operation_log", "user_id", nullable=False)

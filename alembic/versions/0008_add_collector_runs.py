"""add collector_runs

Revision ID: 0008_add_collector_runs
Revises: 0007_add_xhs_account_type
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_add_collector_runs"
down_revision = "0007_add_xhs_account_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS collector_runs (
            id SERIAL PRIMARY KEY,
            platform VARCHAR(16) NOT NULL,
            account_id INTEGER,
            content_type VARCHAR(16),
            started_at TIMESTAMP NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMP,
            status VARCHAR(32) NOT NULL,
            rows_upserted INTEGER NOT NULL DEFAULT 0,
            filename VARCHAR(500),
            error_message TEXT,
            triggered_by VARCHAR(16) NOT NULL DEFAULT 'schedule'
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_collector_runs_started_at ON collector_runs (started_at)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_collector_runs_status ON collector_runs (status)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_collector_runs_platform ON collector_runs (platform)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS collector_runs"))

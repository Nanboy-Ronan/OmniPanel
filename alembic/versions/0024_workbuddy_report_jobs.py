"""Durable WorkBuddy weekly report execution and delivery state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0024_workbuddy_report_jobs"
down_revision = "0023_integration_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workbuddy_report_jobs",
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("weekly_report_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("snapshot", sa.JSON()),
        sa.Column("agent_id", sa.String(200), nullable=False),
        sa.Column("manifest_version", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("runtime_id", sa.String(100)),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("agent_output", sa.JSON()),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("narrative_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("generation_error", sa.String(200)),
        sa.Column("delivery_error", sa.String(200)),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_workbuddy_report_jobs_state", "workbuddy_report_jobs", ["state"])


def downgrade():
    op.drop_table("workbuddy_report_jobs")

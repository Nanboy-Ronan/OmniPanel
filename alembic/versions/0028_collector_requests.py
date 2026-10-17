"""On-demand collect requests and portal display names used by login re-checks."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision = "0028_collector_requests"
down_revision = "0027_workbuddy_hardening"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("collector_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("account_id", sa.Integer()),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)))
    # At most one open request per target: repeated repairs or clicks coalesce.
    op.create_index("uq_collector_requests_open", "collector_requests",
                    ["platform", sa.text("coalesce(account_id, 0)")], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.add_column("xhs_accounts", sa.Column("portal_name", sa.String(200)))
    op.add_column("xhs_accounts", sa.Column("pgy_portal_name", sa.String(200)))
    op.add_column("wx_channels_accounts", sa.Column("portal_name", sa.String(200)))

def downgrade():
    op.drop_column("wx_channels_accounts", "portal_name")
    op.drop_column("xhs_accounts", "pgy_portal_name")
    op.drop_column("xhs_accounts", "portal_name")
    op.drop_index("uq_collector_requests_open", table_name="collector_requests")
    op.drop_table("collector_requests")

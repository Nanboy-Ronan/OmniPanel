"""Account owners and short-lived local login repair requests."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision = "0026_collector_repair"
down_revision = "0025_workbuddy_oauth"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("collector_owners",
        sa.Column("platform", sa.String(20), primary_key=True),
        sa.Column("account_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False))
    op.create_table("collector_repairs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE")),
        sa.Column("platform", sa.String(20)),
        sa.Column("account_id", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"))

def downgrade():
    op.drop_table("collector_repairs")
    op.drop_table("collector_owners")

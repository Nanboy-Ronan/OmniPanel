"""Columns for code-replay revocation, repair pairing codes and bounded report retries."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision = "0027_workbuddy_hardening"
down_revision = "0026_collector_repair"
branch_labels = None
depends_on = None

def upgrade():
    # The token minted from an authorization code, so a replayed code revokes it (RFC 6749 §4.1.2).
    op.add_column("workbuddy_oauth_requests", sa.Column(
        "issued_token_id", UUID(as_uuid=True), sa.ForeignKey("integration_tokens.id", ondelete="SET NULL")))
    op.add_column("workbuddy_oauth_requests", sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("collector_repairs", sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("collector_repairs", sa.Column("user_code", sa.String(16)))
    op.add_column("workbuddy_report_jobs", sa.Column(
        "prepare_attempts", sa.Integer(), nullable=False, server_default="0"))

def downgrade():
    op.drop_column("workbuddy_report_jobs", "prepare_attempts")
    op.drop_column("collector_repairs", "user_code")
    op.drop_column("collector_repairs", "created_at")
    op.drop_column("workbuddy_oauth_requests", "created_at")
    op.drop_column("workbuddy_oauth_requests", "issued_token_id")

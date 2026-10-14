"""Persistent public OAuth clients, PKCE requests and rotating refresh grants."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0025_workbuddy_oauth"
down_revision = "0024_workbuddy_report_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workbuddy_oauth_clients",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("workbuddy_oauth_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("workbuddy_oauth_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("challenge", sa.String(43), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE")),
        sa.Column("code_hash", sa.String(64), unique=True),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)))
    op.create_table("workbuddy_oauth_grants",
        sa.Column("token_id", UUID(as_uuid=True), sa.ForeignKey("integration_tokens.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("workbuddy_oauth_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("workbuddy_oauth_refresh",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("grant_id", UUID(as_uuid=True), sa.ForeignKey("workbuddy_oauth_grants.token_id", ondelete="CASCADE"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)))
    op.create_index("ix_workbuddy_oauth_refresh_grant_id", "workbuddy_oauth_refresh", ["grant_id"])


def downgrade():
    for table in ("workbuddy_oauth_refresh", "workbuddy_oauth_grants", "workbuddy_oauth_requests", "workbuddy_oauth_clients"):
        op.drop_table(table)

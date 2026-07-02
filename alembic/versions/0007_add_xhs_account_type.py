"""add account_type to xhs_accounts

Revision ID: 0007_add_xhs_account_type
Revises: 0006_add_zhihu_posts
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_add_xhs_account_type"
down_revision = "0006_add_zhihu_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE xhs_accounts
        ADD COLUMN IF NOT EXISTS account_type VARCHAR(20) NOT NULL DEFAULT 'company'
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE xhs_accounts DROP COLUMN IF EXISTS account_type"))

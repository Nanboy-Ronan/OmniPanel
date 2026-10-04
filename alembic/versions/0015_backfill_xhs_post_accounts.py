"""Assign legacy XHS posts to an account and enforce the model's foreign key.

Revision ID: 0015_backfill_xhs_post_accounts
Revises: 0014_add_xhs_account_overview
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_backfill_xhs_post_accounts"
down_revision = "0014_add_xhs_account_overview"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        DECLARE legacy_account_id INTEGER;
        BEGIN
            IF EXISTS (SELECT 1 FROM xhs_posts WHERE account_id IS NULL) THEN
                INSERT INTO xhs_accounts (name)
                VALUES ('未归属历史笔记')
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id INTO legacy_account_id;
                UPDATE xhs_posts SET account_id = legacy_account_id
                WHERE account_id IS NULL;
            END IF;
        END $$;
    """))
    op.alter_column("xhs_posts", "account_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.alter_column("xhs_posts", "account_id", existing_type=sa.Integer(), nullable=True)

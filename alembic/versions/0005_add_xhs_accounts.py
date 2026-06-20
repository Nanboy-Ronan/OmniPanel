"""add xhs_accounts table and account_id to xhs_posts

Revision ID: 0005_add_xhs_accounts
Revises: 0004_add_xhs_posts
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_add_xhs_accounts"
down_revision = "0004_add_xhs_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. New xhs_accounts table
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS xhs_accounts (
            id         SERIAL PRIMARY KEY,
            name       VARCHAR(200) NOT NULL UNIQUE,
            is_active  BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    # 2. Add account_id to xhs_posts (nullable first so existing rows are safe)
    conn.execute(sa.text(
        "ALTER TABLE xhs_posts ADD COLUMN IF NOT EXISTS account_id INTEGER"
        " REFERENCES xhs_accounts(id) ON DELETE CASCADE"
    ))

    # 3. Drop old dedup constraint and replace with account-scoped one
    conn.execute(sa.text(
        "ALTER TABLE xhs_posts DROP CONSTRAINT IF EXISTS uq_xhs_posts_title_date"
    ))
    # Guard: DO $$ ... $$ block lets us skip if constraint already exists
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_xhs_posts_account_title_date'
            ) THEN
                ALTER TABLE xhs_posts ADD CONSTRAINT uq_xhs_posts_account_title_date
                    UNIQUE (account_id, title, publish_date);
            END IF;
        END $$
    """))

    # 4. Index on account_id
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_xhs_posts_account_id ON xhs_posts(account_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_xhs_posts_account_id"))
    conn.execute(sa.text(
        "ALTER TABLE xhs_posts DROP CONSTRAINT IF EXISTS uq_xhs_posts_account_title_date"
    ))
    conn.execute(sa.text(
        "ALTER TABLE xhs_posts ADD CONSTRAINT uq_xhs_posts_title_date"
        " UNIQUE (title, publish_date)"
    ))
    conn.execute(sa.text("ALTER TABLE xhs_posts DROP COLUMN IF EXISTS account_id"))
    op.drop_table("xhs_accounts")

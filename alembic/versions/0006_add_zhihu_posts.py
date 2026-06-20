"""add zhihu_posts table

Revision ID: 0006_add_zhihu_posts
Revises: 0005_add_xhs_accounts
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_add_zhihu_posts"
down_revision = "0005_add_xhs_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS zhihu_posts (
            id           SERIAL PRIMARY KEY,
            content_type VARCHAR(10)  NOT NULL,
            title        VARCHAR(500) NOT NULL,
            publish_date DATE         NOT NULL,
            url          VARCHAR(500),
            reads        INTEGER,
            plays        INTEGER,
            likes        INTEGER,
            favorites    INTEGER,
            comments     INTEGER,
            collects     INTEGER,
            shares       INTEGER,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_zhihu_posts_type_title_date'
            ) THEN
                ALTER TABLE zhihu_posts ADD CONSTRAINT uq_zhihu_posts_type_title_date
                    UNIQUE (content_type, title, publish_date);
            END IF;
        END $$
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_zhihu_posts_content_type ON zhihu_posts(content_type)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_zhihu_posts_publish_date ON zhihu_posts(publish_date)"
    ))


def downgrade() -> None:
    op.drop_table("zhihu_posts")

"""add xhs_posts table

Revision ID: 0004_add_xhs_posts
Revises: 0003_add_saved_query
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_add_xhs_posts"
down_revision = "0003_add_saved_query"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS xhs_posts (
            id               SERIAL PRIMARY KEY,
            title            VARCHAR(500) NOT NULL,
            publish_date     DATE NOT NULL,
            genre            VARCHAR(32),
            impressions      INTEGER,
            views            INTEGER,
            cover_click_rate DOUBLE PRECISION,
            likes            INTEGER,
            comments         INTEGER,
            collects         INTEGER,
            new_followers    INTEGER,
            shares           INTEGER,
            avg_watch_time   DOUBLE PRECISION,
            danmu            INTEGER,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_xhs_posts_title_date UNIQUE (title, publish_date)
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_xhs_posts_publish_date ON xhs_posts(publish_date)"
    ))


def downgrade() -> None:
    op.drop_index("ix_xhs_posts_publish_date", "xhs_posts")
    op.drop_table("xhs_posts")

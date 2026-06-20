"""add saved_query table

Revision ID: 0003_add_saved_query
Revises: 3c1fed8ef3cd
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_add_saved_query"
down_revision = "3c1fed8ef3cd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS saved_query (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            name        VARCHAR(200) NOT NULL,
            filters_json JSONB NOT NULL DEFAULT '{}',
            is_shared   BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_saved_query_user_id ON saved_query(user_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_saved_query_shared ON saved_query(is_shared)"
    ))


def downgrade() -> None:
    op.drop_index("ix_saved_query_shared", "saved_query")
    op.drop_index("ix_saved_query_user_id", "saved_query")
    op.drop_table("saved_query")

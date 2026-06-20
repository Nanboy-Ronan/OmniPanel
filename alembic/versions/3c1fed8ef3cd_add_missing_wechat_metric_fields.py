"""add missing wechat metric fields

Revision ID: 3c1fed8ef3cd
Revises: 0001_baseline
Create Date: 2026-05-28 20:02:09.075119

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3c1fed8ef3cd'
down_revision: Union[str, None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLS = [
    ("publish_type", "INTEGER"),
    ("zaikan_user", "INTEGER"),
    ("read_subscribe_user", "INTEGER"),
    ("read_delivery_rate", "DOUBLE PRECISION"),
    ("praise_money", "INTEGER"),
    ("read_jump_position", "JSONB"),
    ("read_finish_rate", "DOUBLE PRECISION"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for col, col_type in _NEW_COLS:
        conn.execute(
            sa.text(
                f"ALTER TABLE media_post_metrics_daily"
                f" ADD COLUMN IF NOT EXISTS {col} {col_type}"
            )
        )


def downgrade() -> None:
    for col, _ in reversed(_NEW_COLS):
        op.drop_column("media_post_metrics_daily", col)

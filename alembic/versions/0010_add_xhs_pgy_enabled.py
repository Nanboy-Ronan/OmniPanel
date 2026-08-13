"""add pgy_enabled to xhs_accounts

Revision ID: 0010_add_xhs_pgy_enabled
Revises: 0009_add_pgy_notes
Create Date: 2026-08-01 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010_add_xhs_pgy_enabled'
down_revision: Union[str, None] = '0009_add_pgy_notes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Defaults to false for every existing account — not every XHS account
    # has a Pugongying (蒲公英) login. Toggle it per account from the admin
    # UI once that account is actually onboarded for collection.
    op.execute('''
    ALTER TABLE xhs_accounts
        ADD COLUMN IF NOT EXISTS pgy_enabled BOOLEAN NOT NULL DEFAULT false;
    ''')


def downgrade() -> None:
    op.execute('ALTER TABLE xhs_accounts DROP COLUMN IF EXISTS pgy_enabled;')

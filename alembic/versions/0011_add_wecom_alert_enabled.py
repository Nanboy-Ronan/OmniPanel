"""add wecom_alert_enabled to user

Revision ID: 0011_add_wecom_alert_enabled
Revises: 0010_add_xhs_pgy_enabled
Create Date: 2026-08-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011_add_wecom_alert_enabled'
down_revision: Union[str, None] = '0010_add_xhs_pgy_enabled'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    ALTER TABLE "user"
        ADD COLUMN IF NOT EXISTS wecom_alert_enabled BOOLEAN NOT NULL DEFAULT false;
    ''')
    # Preserve today's behavior (WECOM_ALERT_TOUSER=@all reaches every WeCom-linked
    # user) until an admin narrows it down via the UI — opt in everyone who
    # already has a linked WeCom account.
    op.execute('''
    UPDATE "user" SET wecom_alert_enabled = true WHERE wecom_userid IS NOT NULL;
    ''')


def downgrade() -> None:
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS wecom_alert_enabled;')

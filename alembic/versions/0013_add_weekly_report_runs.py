"""add weekly_report_runs

Revision ID: 0013_add_weekly_report_runs
Revises: 0012_add_wx_channels
Create Date: 2026-09-10 00:00:00.000000

One row per ISO week (Monday-Sunday) of the 公众号+小红书 weekly media
report. Upserted on retry (see app/reports/service.py) — the UNIQUE on
week_start is the idempotency gate the scheduler checks, keyed on
status='success' rather than mere row existence, so a transient failure
doesn't permanently suppress that week's report.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0013_add_weekly_report_runs'
down_revision: Union[str, None] = '0012_add_wx_channels'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    CREATE TABLE IF NOT EXISTS weekly_report_runs (
        id SERIAL PRIMARY KEY,
        week_start DATE NOT NULL UNIQUE,
        week_end DATE NOT NULL,
        generated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        status VARCHAR(32) NOT NULL,
        html_content TEXT,
        narrative TEXT,
        wecom_sent BOOLEAN NOT NULL DEFAULT false,
        error_message TEXT
    );
    ''')
    op.execute('''
    CREATE INDEX IF NOT EXISTS ix_weekly_report_runs_week_start
        ON weekly_report_runs (week_start);
    ''')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS weekly_report_runs;')

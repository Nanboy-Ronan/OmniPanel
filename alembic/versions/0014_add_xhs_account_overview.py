"""add xhs_account_daily_metrics, xhs_audience_source_daily

Revision ID: 0014_add_xhs_account_overview
Revises: 0013_add_weekly_report_runs
Create Date: 2026-09-11 00:00:00.000000

Backs collect_xhs_overview() (app/collector/xhs.py), the Phase 2 collector
for XHS's "数据概览" page (`/statistics/account/v2`) — account-level daily
metrics (新增/取消/净增关注, 完播率, 人均观看时长, ...) that XhsPost's
per-note, overwrite-on-upsert rows cannot express. Both tables are
append/upsert-only, keyed to self-heal a missed collection day rather than
accumulate duplicates.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014_add_xhs_account_overview'
down_revision: Union[str, None] = '0013_add_weekly_report_runs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    CREATE TABLE IF NOT EXISTS xhs_account_daily_metrics (
        id SERIAL PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES xhs_accounts(id) ON DELETE CASCADE,
        metric_date DATE NOT NULL,
        rise_fans_count INTEGER,
        loss_fans_count INTEGER,
        net_rise_fans_count INTEGER,
        view_count INTEGER,
        view_time_total_seconds INTEGER,
        avg_view_time_seconds DOUBLE PRECISION,
        home_view_count INTEGER,
        like_count INTEGER,
        collect_count INTEGER,
        comment_count INTEGER,
        share_count INTEGER,
        danmaku_count INTEGER,
        cover_click_rate DOUBLE PRECISION,
        video_full_view_rate DOUBLE PRECISION,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        CONSTRAINT uq_xhs_account_daily_metrics_account_date UNIQUE (account_id, metric_date)
    );
    ''')
    op.execute('''
    CREATE INDEX IF NOT EXISTS ix_xhs_account_daily_metrics_account_id
        ON xhs_account_daily_metrics (account_id);
    ''')
    op.execute('''
    CREATE INDEX IF NOT EXISTS ix_xhs_account_daily_metrics_metric_date
        ON xhs_account_daily_metrics (metric_date);
    ''')

    op.execute('''
    CREATE TABLE IF NOT EXISTS xhs_audience_source_daily (
        id SERIAL PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES xhs_accounts(id) ON DELETE CASCADE,
        snapshot_date DATE NOT NULL,
        window_label VARCHAR(8) NOT NULL,
        source_type INTEGER NOT NULL,
        title VARCHAR(64),
        value_pct DOUBLE PRECISION,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        CONSTRAINT uq_xhs_audience_source_daily_account_date_window_source
            UNIQUE (account_id, snapshot_date, window_label, source_type)
    );
    ''')
    op.execute('''
    CREATE INDEX IF NOT EXISTS ix_xhs_audience_source_daily_account_id
        ON xhs_audience_source_daily (account_id);
    ''')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS xhs_audience_source_daily;')
    op.execute('DROP TABLE IF EXISTS xhs_account_daily_metrics;')

"""add pgy_notes

Revision ID: 0009_add_pgy_notes
Revises: 3c1fed8ef3cd
Create Date: 2026-07-30 21:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009_add_pgy_notes'
down_revision: Union[str, None] = '0008_add_collector_runs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    CREATE TABLE IF NOT EXISTS pgy_notes (
        id SERIAL PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES xhs_accounts(id) ON DELETE CASCADE,
        blogger_nickname VARCHAR(200),
        blogger_url VARCHAR(500),
        blogger_fans INTEGER,
        blogger_health VARCHAR(50),
        note_title VARCHAR(500),
        note_url VARCHAR(500),
        note_type VARCHAR(32),
        publish_date DATE,
        note_source VARCHAR(50),
        note_id VARCHAR(100) NOT NULL,
        content_tag VARCHAR(100),
        order_id VARCHAR(100),
        cooperation_name VARCHAR(500),
        report_brand VARCHAR(200),
        order_account VARCHAR(200),
        blogger_quote FLOAT,
        service_fee FLOAT,
        is_premium_mode VARCHAR(10),
        spu_name VARCHAR(200),
        impressions INTEGER,
        reads INTEGER,
        read_uv INTEGER,
        play_rate_5s VARCHAR(20),
        read_rate_3s VARCHAR(20),
        video_duration FLOAT,
        avg_view_duration FLOAT,
        video_completion_rate VARCHAR(20),
        interactions INTEGER,
        interaction_rate VARCHAR(20),
        likes INTEGER,
        collects INTEGER,
        comments INTEGER,
        shares INTEGER,
        follows INTEGER,
        organic_impressions INTEGER,
        organic_reads INTEGER,
        paid_impressions INTEGER,
        paid_reads INTEGER,
        boosted_impressions INTEGER,
        boosted_reads INTEGER,
        cost_per_read FLOAT,
        cost_per_interaction FLOAT,
        fan_ratio VARCHAR(20),
        female_ratio VARCHAR(20),
        male_ratio VARCHAR(20),
        audience_json TEXT,
        component_json TEXT,
        data_date DATE,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        CONSTRAINT uq_pgy_notes_account_note_id UNIQUE (account_id, note_id)
    );
    ''')
    
    op.execute('CREATE INDEX IF NOT EXISTS ix_pgy_notes_account_id ON pgy_notes (account_id);')
    op.execute('CREATE INDEX IF NOT EXISTS ix_pgy_notes_publish_date ON pgy_notes (publish_date);')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS pgy_notes CASCADE;')

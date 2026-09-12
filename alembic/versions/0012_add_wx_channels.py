"""add wx_channels_accounts and wx_channels_posts

Revision ID: 0012_add_wx_channels
Revises: 0011_add_wecom_alert_enabled
Create Date: 2026-08-13 00:00:00.000000

wx_channels_posts' column set was verified live 2026-08-13 against a real
视频号助手 export (see data/channels_example.csv / app/db/models.py's
WxChannelsPost docstring) — this file was rewritten once, before it was ever
deployed, to match reality instead of the initial unverified guess.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012_add_wx_channels'
down_revision: Union[str, None] = '0011_add_wecom_alert_enabled'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    CREATE TABLE IF NOT EXISTS wx_channels_accounts (
        id SERIAL PRIMARY KEY,
        name VARCHAR(200) NOT NULL UNIQUE,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
    );
    ''')

    op.execute('''
    CREATE TABLE IF NOT EXISTS wx_channels_posts (
        id SERIAL PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES wx_channels_accounts(id) ON DELETE CASCADE,
        video_id VARCHAR(255) NOT NULL,
        title TEXT NOT NULL,
        publish_date DATE,
        plays INTEGER,
        recommends INTEGER,
        likes_thumb INTEGER,
        comments INTEGER,
        shares INTEGER,
        new_fans INTEGER,
        forwards_chat_moments INTEGER,
        set_as_ringtone INTEGER,
        set_as_status INTEGER,
        set_as_moments_cover INTEGER,
        wecom_link_clicks INTEGER,
        wecom_link_click_users INTEGER,
        added_to_contacts INTEGER,
        added_to_contacts_users INTEGER,
        avg_watch_duration FLOAT,
        completion_rate FLOAT,
        raw_payload JSON,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
        CONSTRAINT uq_wx_channels_posts_account_video_id UNIQUE (account_id, video_id)
    );
    ''')

    op.execute('CREATE INDEX IF NOT EXISTS ix_wx_channels_posts_account_id ON wx_channels_posts (account_id);')
    op.execute('CREATE INDEX IF NOT EXISTS ix_wx_channels_posts_publish_date ON wx_channels_posts (publish_date);')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS wx_channels_posts CASCADE;')
    op.execute('DROP TABLE IF EXISTS wx_channels_accounts CASCADE;')

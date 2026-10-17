"""Regression tests for defects found in the 2026-10 industry-practice review."""
import asyncio
from datetime import date

from sqlalchemy import func, select

from test_api_endpoints import client, tokens  # noqa: F401


def test_clear_db_keeps_the_audit_trail(client, tokens, monkeypatch):
    from app import db
    from app.db.models import OperationLog
    monkeypatch.setattr("app.views.admin.backup_database", lambda label: "/tmp/synthetic.sql")

    async def count():
        async with db.AsyncSessionLocal() as session:
            return (await session.execute(select(func.count()).select_from(OperationLog))).scalar_one()
    before = asyncio.run(count())
    assert before > 0  # sign-ins of the fixture users
    r = client.post("/admin/clear-db", headers={"Authorization": "Bearer " + tokens["admin"]})
    assert r.status_code == 200, r.text
    assert asyncio.run(count()) >= before + 1  # nothing removed, the clear itself recorded


def test_media_ratios_are_volume_weighted_and_null_when_unknown(client, pg_async_url):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.db.models import WxChannelsAccount, WxChannelsPost, XhsAccount, XhsPost
    from app.services.media_metrics import channels_metrics, xhs_metrics
    from app.views.media.xhs import XhsOverview
    day = date(2026, 9, 8)

    async def run():
        # Own engine: the client's background loops hold connections on another event loop.
        engine = create_async_engine(pg_async_url, poolclass=NullPool)
        try:
            async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
                xhs, ch = XhsAccount(name="ratio-xhs"), WxChannelsAccount(name="ratio-ch")
                session.add_all([xhs, ch])
                await session.flush()
                session.add_all([
                    XhsPost(account_id=xhs.id, title="a", publish_date=day, impressions=900, cover_click_rate=10.0),
                    XhsPost(account_id=xhs.id, title="b", publish_date=day, impressions=100, cover_click_rate=20.0),
                    XhsPost(account_id=xhs.id, title="c", publish_date=day, impressions=5000, cover_click_rate=None),
                    WxChannelsPost(account_id=ch.id, video_id="v1", title="v", publish_date=day, plays=10,
                                   completion_rate=None),
                ])
                await session.commit()
                return (await xhs_metrics(session, day, day, xhs.id),
                        await channels_metrics(session, day, day, ch.id),
                        await xhs_metrics(session, date(2020, 1, 1), date(2020, 1, 2)))
        finally:
            await engine.dispose()

    xhs, ch, empty = asyncio.run(run())
    assert abs(xhs["avg_cover_click_rate"] - 11.0) < 1e-9  # was 10.0: unweighted mean with NULL as 0
    assert ch["avg_completion_rate"] is None  # was 0.0
    assert XhsOverview.model_validate(empty).avg_cover_click_rate is None

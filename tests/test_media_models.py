from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.db.models import MediaAccount, MediaPost, MediaPostMetricDaily, MediaSyncRun


def test_media_tables_are_created(pg_sync_url):
    engine = create_engine(pg_sync_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert {
        "media_accounts",
        "media_posts",
        "media_post_metrics_daily",
        "media_sync_runs",
    }.issubset(tables)


def test_media_models_support_account_post_metric_flow(pg_sync_url):
    engine = create_engine(pg_sync_url, future=True)
    Session = sessionmaker(bind=engine)
    try:
        with Session() as session:
            account = MediaAccount(
                platform="wechat_official",
                name="Test Official Account",
                app_id="wx-test",
                is_active=True,
            )
            session.add(account)
            session.flush()

            post = MediaPost(
                account_id=account.id,
                platform="wechat_official",
                external_id="msg-1:item-0",
                title="Launch article",
                url="https://mp.weixin.qq.com/s/test",
                publish_date="2026-05-20",
                author="Team",
            )
            session.add(post)
            session.flush()

            metric = MediaPostMetricDaily(
                post_id=post.id,
                metric_date="2026-05-21",
                read_count=120,
                read_user_count=90,
                share_count=8,
                share_user_count=6,
                add_to_fav_count=5,
                raw_payload={"source": "unit-test"},
            )
            run = MediaSyncRun(
                account_id=account.id,
                status="success",
                start_date="2026-05-20",
                end_date="2026-05-21",
                posts_upserted=1,
                metrics_upserted=1,
            )
            session.add_all([metric, run])
            session.commit()

            stored = session.query(MediaPost).filter_by(external_id="msg-1:item-0").one()
            assert stored.account.name == "Test Official Account"
            assert stored.metrics[0].read_count == 120
            assert account.sync_runs[0].status == "success"
    finally:
        engine.dispose()

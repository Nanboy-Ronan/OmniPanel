"""Failure boundaries that existing happy-path tests did not exercise."""

import asyncio
import time
import uuid
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


def test_wechat_network_wait_does_not_block_other_requests(monkeypatch):
    from app.services import wechat as routes

    def slow_fetch(*_args):
        time.sleep(0.1)
        return []

    monkeypatch.setattr(
        routes, "WeChatOfficialClient",
        lambda *_args: SimpleNamespace(fetch_article_total_rows=slow_fetch),
    )
    monkeypatch.setattr(routes, "_wechat_secret_for_account", lambda _account: "test")
    session = SimpleNamespace(add=lambda _row: None, flush=AsyncMock())
    account = SimpleNamespace(id=1, name="test", platform="wechat_official", app_id="test")

    async def run():
        progress = []

        async def other_request():
            await asyncio.sleep(0.02)
            progress.append(True)

        sync_task = asyncio.create_task(
            routes._sync_one_wechat_account(session, account, date(2026, 1, 1), date(2026, 1, 2))
        )
        await other_request()
        assert progress and not sync_task.done()
        await sync_task

    asyncio.run(run())


def test_wechat_account_transactions_keep_successes(pg_async_url, monkeypatch):
    import app.db as db
    import app.scheduler as scheduler
    from app.db.models import MediaAccount, MediaSyncRun
    from app.services import wechat as routes

    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", factory)
    messages = []

    async def notify(message):
        messages.append(message)

    async def run():
        async with factory() as session:
            session.add_all([
                MediaAccount(platform="wechat_official", name=name, app_id=name)
                for name in ("good-a", "bad", "good-c")
            ])
            await session.commit()

        async def accounts(session):
            return (await session.execute(select(MediaAccount).order_by(MediaAccount.id))).scalars().all()

        async def sync(session, account, start_date, end_date):
            if account.name == "bad":
                raise RuntimeError("simulated network error")
            session.add(MediaSyncRun(
                account_id=account.id, status="success", start_date=start_date, end_date=end_date
            ))
            return {"posts_upserted": 1, "metrics_upserted": 1}

        monkeypatch.setattr(routes, "_ensure_env_wechat_accounts", accounts)
        monkeypatch.setattr(routes, "_sync_one_wechat_account", sync)
        monkeypatch.setattr(scheduler, "_notify_wecom", notify)
        await scheduler._run_wechat_sync_once(SimpleNamespace(
            wechat_auto_sync_window_days=170, wecom_notify_success=True
        ))

        async with factory() as session:
            result = await session.execute(
                select(MediaAccount.name, MediaSyncRun.status)
                .join(MediaSyncRun, MediaSyncRun.account_id == MediaAccount.id)
            )
            assert set(result.all()) == {
                ("good-a", "success"), ("bad", "failed"), ("good-c", "success")
            }
        assert len(messages) == 1 and "成功的账号" in messages[0]
        await engine.dispose()

    asyncio.run(run())


def test_manual_wechat_sync_continues_after_rollback(pg_async_url, monkeypatch):
    from app.db.models import MediaAccount, MediaSyncRun
    from app.views.media import routes

    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def accounts(session):
        return (await session.execute(select(MediaAccount).order_by(MediaAccount.id))).scalars().all()

    async def sync(session, account, start_date, end_date):
        if account.name == "bad":
            raise RuntimeError("simulated failure")
        session.add(MediaSyncRun(
            account_id=account.id, status="success", start_date=start_date, end_date=end_date
        ))
        return {"posts_upserted": 1, "metrics_upserted": 1}

    monkeypatch.setattr(routes, "_ensure_env_wechat_accounts", accounts)
    monkeypatch.setattr(routes, "_sync_one_wechat_account", sync)

    async def run():
        async with factory() as session:
            session.add_all([
                MediaAccount(platform="wechat_official", name=name, app_id=f"manual-{name}")
                for name in ("good-a", "bad", "good-c")
            ])
            await session.commit()
            result = await routes.sync_wechat_official(
                routes.WeChatSyncRequest(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
                _u=None, session=session,
            )
            assert result["status"] == "partial" and result["accounts_synced"] == 2
            rows = (await session.execute(
                select(MediaAccount.name, MediaSyncRun.status)
                .join(MediaSyncRun, MediaSyncRun.account_id == MediaAccount.id)
            )).all()
            assert set(rows) == {("good-a", "success"), ("bad", "failed"), ("good-c", "success")}
        await engine.dispose()

    asyncio.run(run())


def test_committed_upload_keeps_completed_status_after_cache_failure(pg_async_url, monkeypatch, tmp_path):
    import app.db as db
    from app.db.models import Order, UploadBatch
    from app.views.ecommerce import upload

    async_engine = create_async_engine(pg_async_url, poolclass=NullPool)
    sync_engine = create_engine(pg_async_url.replace("+asyncpg", "+psycopg2"), poolclass=NullPool)
    async_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    sync_factory = sessionmaker(sync_engine, expire_on_commit=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", async_factory)
    monkeypatch.setattr(db, "SyncSessionLocal", sync_factory)
    monkeypatch.setattr(upload.analysis_cache, "invalidate", AsyncMock(side_effect=RuntimeError("cache down")))
    monkeypatch.setattr(upload, "log_operation", AsyncMock())
    path = tmp_path / "orders.csv"
    path.write_text(
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "REVIEW-1,2026-01-01,13900001111,Widget,1,49.00\n",
        encoding="utf-8",
    )

    async def run():
        async with async_factory() as session:
            batch = UploadBatch(filename=path.name, platform="unknown", file_sha256="test",
                                row_count=0, status="processing")
            session.add(batch)
            await session.commit()
            batch_id = batch.id
        await upload._run_ingestion(str(path), path.name, str(uuid.uuid4()), "test", batch_id)
        async with async_factory() as session:
            batch = await session.get(UploadBatch, batch_id)
            count = (await session.execute(select(func.count()).select_from(Order))).scalar_one()
            assert (batch.status, batch.inserted_orders, count) == ("completed", 1, 1)
        await async_engine.dispose()

    asyncio.run(run())
    sync_engine.dispose()


def test_clear_db_reports_success_after_post_commit_side_effect_failure(monkeypatch):
    from app.views import admin

    monkeypatch.setattr(admin, "backup_database", lambda *_args: None)
    monkeypatch.setattr(admin.analysis_cache, "invalidate", AsyncMock(side_effect=RuntimeError("cache down")))
    monkeypatch.setattr(admin, "log_operation", AsyncMock(side_effect=RuntimeError("audit down")))
    session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

    response = asyncio.run(admin.clear_database(_user=SimpleNamespace(id="review"), session=session))
    assert response["detail"].startswith("Dropped tables:")
    session.commit.assert_awaited_once()


def test_stale_processing_uploads_are_recovered(pg_async_url):
    from app.db.maintenance import recover_uploads
    from app.db.models import UploadBatch

    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def run():
        async with factory() as session:
            stale = UploadBatch(filename="stale.csv", platform="youzan", file_sha256="stale",
                                row_count=0, status="processing", uploaded_at=datetime(2020, 1, 1))
            fresh = UploadBatch(filename="fresh.csv", platform="youzan", file_sha256="fresh",
                                row_count=0, status="processing", uploaded_at=datetime.now())
            session.add_all([stale, fresh])
            await session.commit()
            assert await recover_uploads(session) == []
            await session.refresh(stale)
            await session.refresh(fresh)
            assert stale.status == "failed" and fresh.status == "processing"
        await engine.dispose()

    asyncio.run(run())


def test_sql_final_result_limit_ignores_literal(pg_sync_url):
    from app.utils.sql_validator import enforce_limit, validate_sql_query

    query = "SELECT generate_series(1, 5001) AS n, 'LIMIT 1' AS label"
    validate_sql_query(query)
    engine = create_engine(pg_sync_url)
    with engine.connect() as connection:
        rows = connection.execute(text(enforce_limit(query))).fetchall()
    engine.dispose()
    assert len(rows) == 5000


def test_weekly_report_escapes_dynamic_html():
    from app.reports.render import render_html
    from app.reports.weekly_media import week_bounds

    context = {
        "bounds": week_bounds(date(2026, 9, 27)), "generated_at": datetime(2026, 9, 27),
        "wechat_sections": [], "xhs_sections": [], "channels_sections": [],
        "zhihu_section": None, "pgy_sections": [], "ecommerce_section": None,
    }
    html = render_html(context, '<em data-review="marker">text</em>')
    assert '<em data-review="marker">' not in html
    assert '&lt;em data-review=&#34;marker&#34;&gt;' in html


def test_paged_post_loader_includes_rows_beyond_first_page():
    from app.ui._helpers import fetch_all_posts

    seen_offsets = []

    def fetch_page(*, limit, offset, **_filters):
        seen_offsets.append(offset)
        values = list(range(offset, min(offset + limit, 1001)))
        return SimpleNamespace(status_code=200, json=lambda: values)

    rows, error = fetch_all_posts(fetch_page)
    assert error is None
    assert len(rows) == 1001
    assert seen_offsets == [0, 1000]


def test_xhs_post_endpoint_pages_beyond_first_thousand(pg_async_url):
    from app.db.models import XhsAccount, XhsPost
    from app.views.media.xhs import list_xhs_posts, xhs_overview

    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def run():
        async with factory() as session:
            account = XhsAccount(name="pagination", account_type="company")
            session.add(account)
            await session.flush()
            session.add_all([
                XhsPost(account_id=account.id, title=f"post-{i}", publish_date=date(2026, 1, 1),
                        impressions=1, views=2)
                for i in range(1001)
            ])
            await session.commit()
            first = await list_xhs_posts(account_id=account.id, start_date=None, end_date=None,
                                         limit=1000, offset=0, _u=None, session=session)
            second = await list_xhs_posts(account_id=account.id, start_date=None, end_date=None,
                                          limit=1000, offset=1000, _u=None, session=session)
            assert len(first) == 1000 and len(second) == 1
            assert len({row["id"] for row in first + second}) == 1001
            summary = await xhs_overview(account_id=account.id, start_date=None, end_date=None,
                                         _u=None, session=session)
            assert (summary.posts, summary.impressions, summary.views) == (1001, 1001, 2002)
        await engine.dispose()

    asyncio.run(run())


def test_weekly_report_persists_before_push_and_retries_failed_push(pg_async_url, monkeypatch):
    import app.reports.service as service
    from app.db.models import WeeklyReportRun
    from app.reports.weekly_media import week_bounds
    from app.scheduler import _weekly_report_due

    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    reference_date = date(2026, 9, 16)
    bounds = week_bounds(reference_date)
    send_attempts = []

    async def context(_session, _date):
        return {"bounds": bounds, "wechat_sections": []}

    def push(_text):
        async def check_persisted():
            async with factory() as verify:
                row = (await verify.execute(select(WeeklyReportRun))).scalar_one()
                assert row.status == "success" and row.html_content == "<html>ready</html>"

        asyncio.run(check_persisted())
        send_attempts.append(True)
        return len(send_attempts) > 1

    monkeypatch.setattr(service, "build_report_context", context)
    monkeypatch.setattr(service, "generate_narrative", AsyncMock(return_value="summary"))
    monkeypatch.setattr(service, "render_html", lambda *_args: "<html>ready</html>")
    monkeypatch.setattr(service, "send_wecom_alert", push)

    async def run():
        async with factory() as session:
            first = await service.generate_weekly_report(session, reference_date)
            assert first.status == "success" and first.wecom_sent is False
        assert await _weekly_report_due(None, reference_date, async_session_factory=factory)
        async with factory() as session:
            second = await service.generate_weekly_report(session, reference_date)
            assert second.wecom_sent is True
        assert not await _weekly_report_due(None, reference_date, async_session_factory=factory)
        async with factory() as session:
            await service.generate_weekly_report(session, reference_date)
        assert len(send_attempts) == 2
        await engine.dispose()

    asyncio.run(run())


def test_weekly_report_does_not_push_if_save_fails(pg_async_url, monkeypatch):
    import app.reports.service as service
    from app.reports.weekly_media import week_bounds

    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    reference_date = date(2026, 9, 16)
    monkeypatch.setattr(service, "build_report_context", AsyncMock(return_value={
        "bounds": week_bounds(reference_date), "wechat_sections": [],
    }))
    monkeypatch.setattr(service, "generate_narrative", AsyncMock(return_value="summary"))
    monkeypatch.setattr(service, "render_html", lambda *_args: "<html>ready</html>")
    pushed = []
    monkeypatch.setattr(service, "send_wecom_alert", lambda *_args: pushed.append(True))

    async def run():
        async with factory() as session:
            session.commit = AsyncMock(side_effect=RuntimeError("database commit failed"))
            try:
                await service.generate_weekly_report(session, reference_date)
            except RuntimeError as exc:
                assert "database commit failed" in str(exc)
            else:
                assert False, "expected the database failure to propagate"
        assert pushed == []
        await engine.dispose()

    asyncio.run(run())


def test_weekly_failure_alert_does_not_suppress_later_success(pg_async_url, monkeypatch):
    import app.reports.service as service
    from app.db.models import WeeklyReportRun
    from app.reports.weekly_media import week_bounds

    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    reference_date = date(2026, 9, 16)
    bounds = week_bounds(reference_date)
    attempts = 0
    sent = []

    async def context(_session, _date):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary data source failure")
        return {"bounds": bounds, "wechat_sections": []}

    monkeypatch.setattr(service, "build_report_context", context)
    monkeypatch.setattr(service, "generate_narrative", AsyncMock(return_value="summary"))
    monkeypatch.setattr(service, "render_html", lambda *_args: "<html>ready</html>")
    monkeypatch.setattr(service, "send_wecom_alert", lambda message: sent.append(message) or True)

    async def run():
        async with factory() as session:
            first = await service.generate_weekly_report(session, reference_date)
            assert first.status == "error" and first.wecom_sent is False
        async with factory() as session:
            second = await service.generate_weekly_report(session, reference_date)
            assert second.status == "success" and second.wecom_sent is True
        assert len(sent) == 2
        assert "失败" in sent[0] and "登录" in sent[1]
        async with factory() as session:
            row = (await session.execute(select(WeeklyReportRun))).scalar_one()
            assert row.notification_text == sent[1]
        await engine.dispose()

    asyncio.run(run())


def test_stale_upload_with_spooled_file_is_reclaimed(pg_async_url, monkeypatch, tmp_path):
    from app.config import settings
    from app.db.maintenance import recover_uploads
    from app.db.models import UploadBatch
    from app.views.ecommerce.upload import upload_spool_path
    from datetime import datetime

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))
    engine = create_async_engine(pg_async_url, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def run():
        async with factory() as session:
            batch = UploadBatch(filename="orders.csv", platform="unknown", file_sha256="a" * 64,
                                row_count=0, status="processing", uploaded_at=datetime(2020, 1, 1))
            session.add(batch)
            await session.commit()
            path = upload_spool_path(batch.id, batch.filename)
            assert path.is_relative_to(tmp_path / "backups")
            path.parent.mkdir(parents=True)
            path.write_text("saved input")
            jobs = await recover_uploads(session)
            assert len(jobs) == 1 and jobs[0][0] == str(path)
            await session.refresh(batch)
            assert batch.status == "recovering"
        await engine.dispose()

    asyncio.run(run())

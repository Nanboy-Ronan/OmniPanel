"""Tests for the weekly media report's deterministic aggregation layer.

The critical thing under test is the cumulative-snapshot-to-weekly-delta
conversion (app/reports/weekly_media.py's module docstring explains why):
MediaPostMetricDaily rows are cumulative-to-date, verified live against a
real production article whose read_user_count only ever increased across
30 days of rows. Summing rows across a week would inflate reads ~7x; these
tests pin the correct behavior (snapshot difference) so a future edit can't
silently regress back to summing.
"""
import asyncio
from datetime import date, timedelta

import pytest

from app.db.models import MediaAccount, MediaPost, MediaPostMetricDaily, XhsAccount, XhsPost
from app.reports.render import render_html
from app.reports.weekly_media import (
    WeekBounds,
    build_report_context,
    build_wechat_section,
    build_xhs_section,
    is_week_due,
    week_bounds,
)


# ── week_bounds / is_week_due (pure functions, no DB) ────────────────────────

def test_week_bounds_returns_most_recently_completed_week():
    # Tuesday 2026-09-15 -> completed week is Mon 09-07 ~ Sun 09-13
    bounds = week_bounds(date(2026, 9, 15))
    assert bounds.this_week_start == date(2026, 9, 7)
    assert bounds.this_week_end == date(2026, 9, 13)
    assert bounds.last_week_start == date(2026, 8, 31)
    assert bounds.last_week_end == date(2026, 9, 6)


def test_week_bounds_stable_across_any_weekday():
    # Whatever day "today" is, the completed week reported on is the same.
    monday_result = week_bounds(date(2026, 9, 14))
    sunday_result = week_bounds(date(2026, 9, 20))
    assert monday_result == sunday_result


def test_is_week_due_respects_lag_days():
    week_end = date(2026, 9, 13)  # a Sunday
    assert is_week_due(week_end, today=date(2026, 9, 14), lag_days=2) is False  # +1 day
    assert is_week_due(week_end, today=date(2026, 9, 15), lag_days=2) is True   # +2 days


# ── build_wechat_section: cumulative -> weekly delta ─────────────────────────

@pytest.fixture
def async_session_factory(pg_async_url):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield SessionLocal
    asyncio.run(engine.dispose())


BOUNDS = WeekBounds(
    this_week_start=date(2026, 9, 7),
    this_week_end=date(2026, 9, 13),
    last_week_start=date(2026, 8, 31),
    last_week_end=date(2026, 9, 6),
)


async def _seed_account(session_factory, name: str) -> int:
    async with session_factory() as session:
        account = MediaAccount(platform="wechat_official", name=name, app_id=f"wx-{name}")
        session.add(account)
        await session.flush()
        account_id = account.id
        await session.commit()
        return account_id


async def _seed_post_with_daily_snapshots(
    session_factory, account_id: int, title: str, publish_date: date, daily_read_user_counts: dict[date, int]
) -> int:
    """daily_read_user_counts maps metric_date -> the CUMULATIVE read_user_count
    reported that day (mirrors the real API's cumulative shape)."""
    async with session_factory() as session:
        post = MediaPost(
            account_id=account_id, platform="wechat_official",
            external_id=f"ext-{title}", title=title, publish_date=publish_date,
        )
        session.add(post)
        await session.flush()
        post_id = post.id
        for metric_date, count in daily_read_user_counts.items():
            session.add(
                MediaPostMetricDaily(
                    post_id=post_id, metric_date=metric_date,
                    read_user_count=count, read_count=count * 2,
                    read_finish_rate=0.4, read_avg_time=30.0,
                )
            )
        await session.commit()
        return post_id


class TestCumulativeToWeeklyDelta:
    def test_weekly_total_is_snapshot_difference_not_sum(self, async_session_factory):
        account_id = asyncio.run(_seed_account(async_session_factory, "acct-delta"))
        # Cumulative read_user_count: 10 the day before this week starts,
        # rising to 50 by the end of this week. The correct weekly figure is
        # 50 - 10 = 40, never the sum of the daily rows (which would be far
        # more than 40 and would scale with how many days have a row).
        daily = {
            BOUNDS.this_week_start - timedelta(days=1): 10,
            BOUNDS.this_week_start: 15,
            BOUNDS.this_week_start + timedelta(days=2): 30,
            BOUNDS.this_week_end: 50,
        }
        asyncio.run(_seed_post_with_daily_snapshots(async_session_factory, account_id, "Article A", date(2026, 8, 1), daily))

        async def _run():
            async with async_session_factory() as session:
                account = await session.get(MediaAccount, account_id)
                return await build_wechat_section(session, account, BOUNDS, wechat_client=None)

        section = asyncio.run(_run())
        assert section["totals_this_week"]["read_user_count"] == 40
        article = section["articles"][0]
        assert article["counts_this_week"]["read_user_count"] == 40

    def test_rate_field_uses_latest_snapshot_not_average(self, async_session_factory):
        account_id = asyncio.run(_seed_account(async_session_factory, "acct-rate"))
        daily = {
            BOUNDS.this_week_start - timedelta(days=1): 10,
            BOUNDS.this_week_end: 20,
        }

        async def _run():
            async with async_session_factory() as session:
                post = MediaPost(
                    account_id=account_id, platform="wechat_official",
                    external_id="ext-rate", title="Rate Article", publish_date=date(2026, 8, 1),
                )
                session.add(post)
                await session.flush()
                session.add(MediaPostMetricDaily(
                    post_id=post.id, metric_date=BOUNDS.this_week_start - timedelta(days=1),
                    read_user_count=10, read_finish_rate=0.9, read_avg_time=10.0,
                ))
                session.add(MediaPostMetricDaily(
                    post_id=post.id, metric_date=BOUNDS.this_week_end,
                    read_user_count=20, read_finish_rate=0.3, read_avg_time=50.0,
                ))
                await session.commit()
                account = await session.get(MediaAccount, account_id)
                return await build_wechat_section(session, account, BOUNDS, wechat_client=None)

        section = asyncio.run(_run())
        article = section["articles"][0]
        # Latest snapshot's rate (0.3), not e.g. the average of 0.9 and 0.3.
        assert article["rates_as_of_this_week"]["read_finish_rate"] == pytest.approx(0.3)
        assert article["rates_as_of_this_week"]["read_avg_time"] == pytest.approx(50.0)

    def test_post_published_this_week_is_complete_not_flagged_as_gap(self, async_session_factory):
        """A post with no snapshot before this_week_start is *expected* — it
        didn't exist yet. Only an older post missing its baseline is a real
        gap. Regression guard for the bug the plan review caught."""
        account_id = asyncio.run(_seed_account(async_session_factory, "acct-new"))
        daily = {BOUNDS.this_week_start + timedelta(days=1): 5}
        post_id = asyncio.run(
            _seed_post_with_daily_snapshots(
                async_session_factory, account_id, "Brand New Article", BOUNDS.this_week_start + timedelta(days=1), daily
            )
        )

        async def _run():
            async with async_session_factory() as session:
                account = await session.get(MediaAccount, account_id)
                return await build_wechat_section(session, account, BOUNDS, wechat_client=None)

        section = asyncio.run(_run())
        article = next(a for a in section["articles"] if a["post_id"] == post_id)
        assert article["is_new_this_week"] is True
        assert article["complete"] is True
        assert article["counts_this_week"]["read_user_count"] == 5

    def test_older_post_missing_baseline_is_flagged_incomplete(self, async_session_factory):
        account_id = asyncio.run(_seed_account(async_session_factory, "acct-gap"))
        # Published well before this week, but its earliest snapshot lands
        # *inside* this week — a genuine gap (e.g. sync window too narrow).
        daily = {BOUNDS.this_week_start + timedelta(days=1): 5}
        post_id = asyncio.run(
            _seed_post_with_daily_snapshots(
                async_session_factory, account_id, "Old Article", date(2026, 1, 1), daily
            )
        )

        async def _run():
            async with async_session_factory() as session:
                account = await session.get(MediaAccount, account_id)
                return await build_wechat_section(session, account, BOUNDS, wechat_client=None)

        section = asyncio.run(_run())
        article = next(a for a in section["articles"] if a["post_id"] == post_id)
        assert article["is_new_this_week"] is False
        assert article["complete"] is False

    def test_no_wechat_client_marks_follower_section_unavailable(self, async_session_factory):
        account_id = asyncio.run(_seed_account(async_session_factory, "acct-nofollower"))

        async def _run():
            async with async_session_factory() as session:
                account = await session.get(MediaAccount, account_id)
                return await build_wechat_section(session, account, BOUNDS, wechat_client=None)

        section = asyncio.run(_run())
        assert section["follower"]["available"] is False


# ── build_xhs_section ─────────────────────────────────────────────────────────

async def _seed_xhs_account_with_posts(session_factory, name: str, posts: list[dict]) -> int:
    async with session_factory() as session:
        account = XhsAccount(name=name)
        session.add(account)
        await session.flush()
        account_id = account.id
        for p in posts:
            session.add(XhsPost(account_id=account_id, **p))
        await session.commit()
        return account_id


class TestWechatSecretResolution:
    """Regression test for a real production bug: MediaAccount.app_secret is
    essentially never populated (real secrets live in env vars, resolved by
    app_id) — build_report_context must resolve the secret the same way the
    manual-sync flow does, not read the (empty) DB column directly."""

    def test_resolves_secret_from_env_not_db_column(self, async_session_factory, monkeypatch):
        account_id = asyncio.run(_seed_account(async_session_factory, "acct-secret"))

        async def _run():
            async with async_session_factory() as session:
                account = await session.get(MediaAccount, account_id)
                assert account.app_secret is None  # never set by _seed_account, matches production
                return account

        account = asyncio.run(_run())

        import app.views.media.routes as routes_mod

        monkeypatch.setattr(
            routes_mod, "_wechat_env_accounts",
            lambda: [{"name": account.name, "app_id": account.app_id, "app_secret": "real-secret-from-env"}],
        )

        captured = {}

        def fake_fetch_user_summary_rows(self, start_date, end_date):
            captured["app_secret"] = self.app_secret
            return []

        monkeypatch.setattr(
            "app.connectors.wechat_official.WeChatOfficialClient.fetch_user_summary_rows",
            fake_fetch_user_summary_rows,
        )

        async def _build():
            async with async_session_factory() as session:
                return await build_report_context(session, reference_date=date(2026, 9, 15))

        context = asyncio.run(_build())
        section = next(s for s in context["wechat_sections"] if s["account"].id == account_id)
        assert section["follower"]["available"] is True
        assert captured["app_secret"] == "real-secret-from-env"


class TestBuilderToTemplateIntegration:
    """build_report_context()'s actual output, piped straight into
    render_html() — not a hand-built fake context. A key rename on either
    side of that seam should break this test, not just the two halves
    tested in isolation."""

    def test_real_context_renders_without_error_and_contains_real_data(self, async_session_factory):
        wechat_account_id = asyncio.run(_seed_account(async_session_factory, "acct-integration"))
        daily = {
            BOUNDS.this_week_start - timedelta(days=1): 10,
            BOUNDS.this_week_end: 42,
        }
        asyncio.run(
            _seed_post_with_daily_snapshots(
                async_session_factory, wechat_account_id, "Integration Test Article", date(2026, 8, 1), daily
            )
        )
        asyncio.run(
            _seed_xhs_account_with_posts(
                async_session_factory,
                "xhs-acct-integration",
                [{"title": "Integration Note", "publish_date": BOUNDS.this_week_start, "views": 77, "avg_watch_time": 4.0, "new_followers": 2}],
            )
        )

        async def _run():
            async with async_session_factory() as session:
                return await build_report_context(session, reference_date=date(2026, 9, 15))

        context = asyncio.run(_run())
        html = render_html(context, narrative="本周表现平稳。")

        assert "Integration Test Article" in html
        assert "32" in html  # this week's read_user_count delta: 42 (end snapshot) - 10 (baseline) = 32
        assert "xhs-acct-integration" in html
        assert "Integration Note" in html
        assert "本周表现平稳。" in html


class TestXhsSection:
    def test_this_week_posts_and_summary(self, async_session_factory):
        account_id = asyncio.run(
            _seed_xhs_account_with_posts(
                async_session_factory,
                "xhs-acct-1",
                [
                    {"title": "Post 1", "publish_date": BOUNDS.this_week_start, "views": 100, "avg_watch_time": 5.0, "new_followers": 3},
                    {"title": "Post 2", "publish_date": BOUNDS.this_week_end, "views": 200, "avg_watch_time": 10.0, "new_followers": 7},
                    {"title": "Last week post", "publish_date": BOUNDS.last_week_start, "views": 50, "avg_watch_time": 2.0, "new_followers": 1},
                ],
            )
        )

        async def _run():
            async with async_session_factory() as session:
                account = await session.get(XhsAccount, account_id)
                return await build_xhs_section(session, account, BOUNDS)

        section = asyncio.run(_run())
        assert section["this_week_summary"]["count"] == 2
        assert section["this_week_summary"]["total_new_followers"] == 10
        assert section["last_week_summary"]["count"] == 1
        # Sorted by views desc.
        assert section["this_week_posts"][0]["title"] == "Post 2"
        assert section["this_week_posts"][0]["estimated_total_watch_time"] == pytest.approx(2000.0)

    def test_no_posts_this_week_returns_empty_not_error(self, async_session_factory):
        account_id = asyncio.run(_seed_xhs_account_with_posts(async_session_factory, "xhs-acct-empty", []))

        async def _run():
            async with async_session_factory() as session:
                account = await session.get(XhsAccount, account_id)
                return await build_xhs_section(session, account, BOUNDS)

        section = asyncio.run(_run())
        assert section["this_week_posts"] == []
        assert section["this_week_summary"]["count"] == 0
        assert "完播率" in section["not_collected"]

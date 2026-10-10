"""Tests for the WeChat auto-sync scheduler helpers."""
import asyncio
import dataclasses
from datetime import datetime
from unittest.mock import patch

import pytest

import app.scheduler as scheduler_mod
from app.scheduler import run_watchdog_checks, seconds_until_next_run


class TestSecondsUntilNextRun:
    def _now_at(self, hour: int, minute: int = 0, second: int = 0, tz: str = "Asia/Shanghai"):
        """Return a fixed datetime in *tz* at the given time-of-day."""
        from zoneinfo import ZoneInfo

        return datetime(2026, 5, 28, hour, minute, second, tzinfo=ZoneInfo(tz))

    def _call(self, target_hour: int, current_hour: int, current_minute: int = 0,
              tz: str = "Asia/Shanghai") -> float:
        fake_now = self._now_at(current_hour, current_minute, tz=tz)
        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            return seconds_until_next_run(target_hour, tz)

    def test_target_later_today(self):
        # It is 01:00 and target is 03:00 → 2 h = 7200 s
        delay = self._call(target_hour=3, current_hour=1)
        assert abs(delay - 7200) < 2

    def test_target_is_now_schedules_tomorrow(self):
        # It is exactly 03:00 and target is 03:00 → should schedule 24 h later
        delay = self._call(target_hour=3, current_hour=3, current_minute=0)
        assert abs(delay - 86400) < 2

    def test_target_already_passed_today(self):
        # It is 10:00 and target is 03:00 → 17 h until 03:00 tomorrow
        delay = self._call(target_hour=3, current_hour=10)
        assert abs(delay - 17 * 3600) < 2

    def test_midnight_target(self):
        # It is 23:00 and target is 00:00 → 1 h
        delay = self._call(target_hour=0, current_hour=23)
        assert abs(delay - 3600) < 2

    def test_unknown_timezone_falls_back_to_utc(self):
        # Should not raise; result is some positive number
        delay = seconds_until_next_run(hour=3, tz_name="Not/AReal/Timezone")
        assert delay > 0

    def test_returns_positive_seconds(self):
        for hour in [0, 3, 12, 23]:
            delay = seconds_until_next_run(hour=hour, tz_name="Asia/Shanghai")
            assert 0 < delay <= 86400


@dataclasses.dataclass
class _FakeSchedulerSettings:
    # WeChat auto-sync
    wechat_auto_sync_window_days: int = 170
    wecom_notify_success: bool = True
    # Watchdog
    collector_enabled: bool = False
    wechat_auto_sync_enabled: bool = False
    rap_disable_monthly_backup: bool = True
    watchdog_max_age_hours: int = 30
    watchdog_backup_max_age_days: int = 35
    watchdog_daily_backup_max_age_days: int = 2
    backup_dir: str = "unused"
    # Collector sources (only xhs by default so tests opt in to the others)
    collector_xhs_enabled: bool = True
    collector_xhs_overview_enabled: bool = False
    collector_zhihu_enabled: bool = False
    collector_pugongying_enabled: bool = False
    collector_channels_enabled: bool = False
    collector_jd_enabled: bool = False
    # Weekly report
    report_hour: int = 9
    app_timezone: str = "Asia/Shanghai"


# ── run_watchdog_checks: collector (CollectorRun via the sync engine) ───────

@pytest.fixture
def sync_session_factory(pg_sync_url):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    engine = create_engine(pg_sync_url, future=True, echo=False)
    SessionLocal = sessionmaker(engine, class_=Session, expire_on_commit=False)
    yield SessionLocal
    engine.dispose()


def _age_collector_run(sync_session_factory, run_id: int, hours_ago: int) -> None:
    from sqlalchemy import text

    with sync_session_factory() as session:
        session.execute(
            text(
                "UPDATE collector_runs SET started_at = now() - make_interval(hours => :hrs) "
                "WHERE id = :id"
            ),
            {"hrs": hours_ago, "id": run_id},
        )
        session.commit()


def _xhs_account(sync_session_factory, name="示例账号", *, pgy_enabled=False, is_active=True) -> int:
    from app.db.models import XhsAccount

    with sync_session_factory() as session:
        acc = XhsAccount(name=name, pgy_enabled=pgy_enabled, is_active=is_active)
        session.add(acc)
        session.commit()
        return acc.id


def _collector_run(sync_session_factory, platform, *, account_id=None, content_type=None,
                   status="success", triggered_by="schedule", hours_ago=None) -> int:
    from app.db.models import CollectorRun

    with sync_session_factory() as session:
        run = CollectorRun(platform=platform, account_id=account_id, content_type=content_type,
                           status=status, triggered_by=triggered_by)
        session.add(run)
        session.commit()
        run_id = run.id
    if hours_ago is not None:
        _age_collector_run(sync_session_factory, run_id, hours_ago)
    return run_id


def _check(settings, sync_session_factory):
    return asyncio.run(run_watchdog_checks(settings, sync_session_factory=sync_session_factory))


class TestRunWatchdogChecksCollector:
    def test_disabled_skips_check(self, sync_session_factory):
        settings = _FakeSchedulerSettings(collector_enabled=False)
        assert _check(settings, sync_session_factory) == []

    def test_no_runs_ever_reports_problem(self, sync_session_factory):
        _xhs_account(sync_session_factory)
        settings = _FakeSchedulerSettings(collector_enabled=True)
        problems = _check(settings, sync_session_factory)
        assert len(problems) == 1
        assert "采集器" in problems[0] and "示例账号" in problems[0]
        assert "从未" in problems[0]

    def test_recent_run_is_healthy(self, sync_session_factory):
        acc = _xhs_account(sync_session_factory)
        _collector_run(sync_session_factory, "xhs", account_id=acc)
        settings = _FakeSchedulerSettings(collector_enabled=True)
        assert _check(settings, sync_session_factory) == []

    def test_stale_run_reports_problem(self, sync_session_factory):
        acc = _xhs_account(sync_session_factory)
        # 72h ago, computed via the DB's own now() so it can't drift from
        # whatever Postgres session timezone this engine happens to use.
        _collector_run(sync_session_factory, "xhs", account_id=acc, hours_ago=72)
        settings = _FakeSchedulerSettings(collector_enabled=True, watchdog_max_age_hours=30)
        problems = _check(settings, sync_session_factory)
        assert len(problems) == 1
        assert "采集器" in problems[0]
        assert "30 小时" in problems[0]

    def test_recent_verify_run_does_not_mask_stale_collect(self, sync_session_factory):
        """An evening verify-all run must not hide a collect that failed/stopped."""
        acc = _xhs_account(sync_session_factory)
        _collector_run(sync_session_factory, "xhs", account_id=acc, hours_ago=72)
        _collector_run(sync_session_factory, "xhs", account_id=acc, triggered_by="verify")
        settings = _FakeSchedulerSettings(collector_enabled=True)
        problems = _check(settings, sync_session_factory)
        assert len(problems) == 1 and "示例账号" in problems[0]

    def test_recent_failed_collect_does_not_count(self, sync_session_factory):
        acc = _xhs_account(sync_session_factory)
        _collector_run(sync_session_factory, "xhs", account_id=acc, hours_ago=72)
        _collector_run(sync_session_factory, "xhs", account_id=acc, status="download_failed")
        settings = _FakeSchedulerSettings(collector_enabled=True)
        assert len(_check(settings, sync_session_factory)) == 1

    def test_one_dead_platform_alerts_while_others_are_fresh(self, sync_session_factory):
        acc = _xhs_account(sync_session_factory)
        _collector_run(sync_session_factory, "xhs", account_id=acc)
        _collector_run(sync_session_factory, "zhihu", content_type="article")
        settings = _FakeSchedulerSettings(collector_enabled=True, collector_zhihu_enabled=True)
        problems = _check(settings, sync_session_factory)
        assert len(problems) == 1
        assert "知乎·问答" in problems[0] and "从未" in problems[0]

    def test_per_account_staleness(self, sync_session_factory):
        fresh = _xhs_account(sync_session_factory, "fresh-acc")
        _xhs_account(sync_session_factory, "dead-acc")
        _xhs_account(sync_session_factory, "inactive-acc", is_active=False)
        _collector_run(sync_session_factory, "xhs", account_id=fresh)
        settings = _FakeSchedulerSettings(collector_enabled=True)
        problems = _check(settings, sync_session_factory)
        assert len(problems) == 1 and "dead-acc" in problems[0]

    def test_disabled_sources_are_not_expected(self, sync_session_factory):
        """视频号 is intentionally disabled, 蒲公英 only runs for pgy_enabled
        accounts and 数据概览 has its own flag — none of those may alert."""
        from app.db.models import WxChannelsAccount

        acc = _xhs_account(sync_session_factory, "no-pgy", pgy_enabled=False)
        with sync_session_factory() as session:
            session.add(WxChannelsAccount(name="视频号账号"))
            session.commit()
        _collector_run(sync_session_factory, "xhs", account_id=acc)
        settings = _FakeSchedulerSettings(
            collector_enabled=True, collector_pugongying_enabled=True,
            collector_channels_enabled=False, collector_xhs_overview_enabled=False,
        )
        assert _check(settings, sync_session_factory) == []

    def test_enabled_pgy_and_overview_sources_are_expected(self, sync_session_factory):
        acc = _xhs_account(sync_session_factory, "示例账号", pgy_enabled=True)
        _collector_run(sync_session_factory, "xhs", account_id=acc)
        settings = _FakeSchedulerSettings(
            collector_enabled=True, collector_pugongying_enabled=True, collector_xhs_overview_enabled=True,
        )
        problems = _check(settings, sync_session_factory)
        assert len(problems) == 2
        assert any("蒲公英·示例账号" in p for p in problems)
        assert any("小红书数据概览·示例账号" in p for p in problems)

    def test_stuck_running_rows_are_reaped_and_reported(self, sync_session_factory):
        from app.db.models import CollectorRun

        acc = _xhs_account(sync_session_factory)
        _collector_run(sync_session_factory, "xhs", account_id=acc)
        stuck = _collector_run(sync_session_factory, "xhs", account_id=acc, status="running", hours_ago=1)
        recent = _collector_run(sync_session_factory, "zhihu", content_type="qa", status="running")
        settings = _FakeSchedulerSettings(collector_enabled=True)
        problems = _check(settings, sync_session_factory)
        assert len(problems) == 1
        assert f"#{stuck}" in problems[0] and "killed" in problems[0]
        with sync_session_factory() as session:
            assert session.get(CollectorRun, stuck).status == "killed"
            assert session.get(CollectorRun, stuck).finished_at is not None
            assert session.get(CollectorRun, recent).status == "running"
        # Already reaped rows are not reported again.
        assert _check(settings, sync_session_factory) == []


# ── run_watchdog_checks: WeChat auto-sync (MediaSyncRun via the async engine) ─

@pytest.fixture
def async_session_factory(pg_async_url):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield SessionLocal
    asyncio.run(engine.dispose())


async def _make_media_sync_run(session_factory, *, source: str = "api", hours_ago: int | None = None) -> None:
    import datetime as dt

    from sqlalchemy import text

    from app.db.models import MediaAccount, MediaSyncRun

    async with session_factory() as session:
        account = MediaAccount(platform="wechat_official", name="test-account", app_id="wx-test")
        session.add(account)
        await session.flush()
        run = MediaSyncRun(
            account_id=account.id,
            status="success",
            start_date=dt.date.today(),
            end_date=dt.date.today(),
            source=source,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        if hours_ago is not None:
            await session.execute(
                text(
                    "UPDATE media_sync_runs SET started_at = now() - make_interval(hours => :hrs) "
                    "WHERE id = :id"
                ),
                {"hrs": hours_ago, "id": run_id},
            )
        await session.commit()


class TestRunWatchdogChecksWechatSync:
    def test_disabled_skips_check(self, async_session_factory):
        settings = _FakeSchedulerSettings(wechat_auto_sync_enabled=False)
        problems = asyncio.run(
            run_watchdog_checks(settings, async_session_factory=async_session_factory)
        )
        assert problems == []

    def test_no_runs_ever_reports_problem(self, async_session_factory):
        settings = _FakeSchedulerSettings(wechat_auto_sync_enabled=True)
        problems = asyncio.run(
            run_watchdog_checks(settings, async_session_factory=async_session_factory)
        )
        assert len(problems) == 1
        assert "微信自动同步" in problems[0]
        assert "从未" in problems[0]

    def test_recent_run_is_healthy(self, async_session_factory):
        asyncio.run(_make_media_sync_run(async_session_factory))
        settings = _FakeSchedulerSettings(wechat_auto_sync_enabled=True)
        problems = asyncio.run(
            run_watchdog_checks(settings, async_session_factory=async_session_factory)
        )
        assert problems == []

    def test_stale_run_reports_problem(self, async_session_factory):
        asyncio.run(_make_media_sync_run(async_session_factory, hours_ago=72))
        settings = _FakeSchedulerSettings(wechat_auto_sync_enabled=True, watchdog_max_age_hours=30)
        problems = asyncio.run(
            run_watchdog_checks(settings, async_session_factory=async_session_factory)
        )
        assert len(problems) == 1
        assert "微信自动同步" in problems[0]

    def test_manual_xlsx_upload_does_not_count_as_a_live_run(self, async_session_factory):
        """A manual xlsx upload writes a fresh media_sync_runs row too, but it
        must not mask an auto-sync pipeline that has actually stopped running."""
        asyncio.run(_make_media_sync_run(async_session_factory, source="xlsx"))
        settings = _FakeSchedulerSettings(wechat_auto_sync_enabled=True)
        problems = asyncio.run(
            run_watchdog_checks(settings, async_session_factory=async_session_factory)
        )
        assert len(problems) == 1
        assert "微信自动同步" in problems[0]


# ── run_watchdog_checks: monthly backup (file-stamp based, no DB) ───────────

class TestRunWatchdogChecksBackup:
    def test_disabled_skips_check(self, tmp_path):
        settings = _FakeSchedulerSettings(rap_disable_monthly_backup=True, backup_dir=str(tmp_path))
        problems = asyncio.run(run_watchdog_checks(settings))
        assert problems == []

    def test_missing_stamp_reports_problem(self, tmp_path):
        settings = _FakeSchedulerSettings(rap_disable_monthly_backup=False, backup_dir=str(tmp_path))
        problems = asyncio.run(run_watchdog_checks(settings))
        assert len(problems) == 3
        assert all("备份" in problem for problem in problems)
        assert sum("从未" in problem for problem in problems) == 2
        assert any("没有任何备份文件" in problem for problem in problems)

    def test_recent_stamp_is_healthy(self, tmp_path):
        (tmp_path / ".last_monthly_backup").write_text(datetime.now().isoformat())
        (tmp_path / ".last_daily_backup").write_text(datetime.now().isoformat())
        (tmp_path / "rpa-20260101-000000-daily.sql").write_bytes(b"--\n-- PostgreSQL database dump\n")
        settings = _FakeSchedulerSettings(rap_disable_monthly_backup=False, backup_dir=str(tmp_path))
        problems = asyncio.run(run_watchdog_checks(settings))
        assert problems == []

    def test_stale_stamp_reports_problem(self, tmp_path):
        from datetime import timedelta

        stale = datetime.now() - timedelta(days=40)
        (tmp_path / ".last_monthly_backup").write_text(stale.isoformat())
        (tmp_path / ".last_daily_backup").write_text(datetime.now().isoformat())
        (tmp_path / "rpa-20260101-000000-daily.sql").write_bytes(b"--\n-- PostgreSQL database dump\n")
        settings = _FakeSchedulerSettings(
            rap_disable_monthly_backup=False, watchdog_backup_max_age_days=35, backup_dir=str(tmp_path)
        )
        problems = asyncio.run(run_watchdog_checks(settings))
        assert len(problems) == 1
        assert "备份" in problems[0]


# ── _run_wechat_sync_once: notify on both success and failure ───────────────

class _FakeAsyncSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


@dataclasses.dataclass
class _FakeMediaAccount:
    name: str


class TestRunWechatSyncOnceNotifications:
    @pytest.fixture(autouse=True)
    def _patch_db(self, monkeypatch):
        import app.db as db_mod

        monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _FakeAsyncSession(), raising=False)

    @pytest.fixture
    def wecom_sent(self, monkeypatch):
        sent = []

        async def _fake_notify(text):
            sent.append(text)

        monkeypatch.setattr(scheduler_mod, "_notify_wecom", _fake_notify)
        return sent

    def test_all_accounts_succeed_sends_success_notification(self, monkeypatch, wecom_sent):
        import app.services.wechat as routes_mod

        async def fake_ensure(session):
            return [_FakeMediaAccount(name="acct1"), _FakeMediaAccount(name="acct2")]

        async def fake_sync(session, account, start_date, end_date):
            return {"posts_upserted": 3, "metrics_upserted": 5}

        monkeypatch.setattr(routes_mod, "_ensure_env_wechat_accounts", fake_ensure)
        monkeypatch.setattr(routes_mod, "_sync_one_wechat_account", fake_sync)

        settings = _FakeSchedulerSettings(wecom_notify_success=True)
        asyncio.run(scheduler_mod._run_wechat_sync_once(settings))

        assert len(wecom_sent) == 1
        assert "同步成功" in wecom_sent[0]
        assert "acct1" in wecom_sent[0]
        assert "acct2" in wecom_sent[0]

    def test_success_notification_suppressed_when_disabled(self, monkeypatch, wecom_sent):
        import app.services.wechat as routes_mod

        async def fake_ensure(session):
            return [_FakeMediaAccount(name="acct1")]

        async def fake_sync(session, account, start_date, end_date):
            return {"posts_upserted": 1, "metrics_upserted": 1}

        monkeypatch.setattr(routes_mod, "_ensure_env_wechat_accounts", fake_ensure)
        monkeypatch.setattr(routes_mod, "_sync_one_wechat_account", fake_sync)

        settings = _FakeSchedulerSettings(wecom_notify_success=False)
        asyncio.run(scheduler_mod._run_wechat_sync_once(settings))

        assert wecom_sent == []

    def test_partial_failure_sends_single_alert_with_both(self, monkeypatch, wecom_sent):
        import app.services.wechat as routes_mod

        async def fake_ensure(session):
            return [_FakeMediaAccount(name="ok_acct"), _FakeMediaAccount(name="bad_acct")]

        async def fake_sync(session, account, start_date, end_date):
            if account.name == "bad_acct":
                raise RuntimeError("boom")
            return {"posts_upserted": 1, "metrics_upserted": 1}

        monkeypatch.setattr(routes_mod, "_ensure_env_wechat_accounts", fake_ensure)
        monkeypatch.setattr(routes_mod, "_sync_one_wechat_account", fake_sync)

        settings = _FakeSchedulerSettings()
        asyncio.run(scheduler_mod._run_wechat_sync_once(settings))

        assert len(wecom_sent) == 1
        assert "告警" in wecom_sent[0]
        assert "bad_acct" in wecom_sent[0]
        assert "ok_acct" in wecom_sent[0]

    def test_all_accounts_fail_sends_failure_alert_only(self, monkeypatch, wecom_sent):
        import app.services.wechat as routes_mod

        async def fake_ensure(session):
            return [_FakeMediaAccount(name="bad_acct")]

        async def fake_sync(session, account, start_date, end_date):
            raise RuntimeError("boom")

        monkeypatch.setattr(routes_mod, "_ensure_env_wechat_accounts", fake_ensure)
        monkeypatch.setattr(routes_mod, "_sync_one_wechat_account", fake_sync)

        settings = _FakeSchedulerSettings()
        asyncio.run(scheduler_mod._run_wechat_sync_once(settings))

        assert len(wecom_sent) == 1
        assert "告警" in wecom_sent[0]
        assert "成功的账号" not in wecom_sent[0]

    def test_no_accounts_configured_sends_nothing(self, monkeypatch, wecom_sent):
        import app.services.wechat as routes_mod

        async def fake_ensure(session):
            return []

        monkeypatch.setattr(routes_mod, "_ensure_env_wechat_accounts", fake_ensure)

        settings = _FakeSchedulerSettings()
        asyncio.run(scheduler_mod._run_wechat_sync_once(settings))

        assert wecom_sent == []


# ── _weekly_report_due: gate is success-only, not row-existence ─────────────

async def _insert_weekly_report_run(session_factory, *, week_start, week_end, status: str,
                                    wecom_sent: bool = False) -> None:
    from app.db.models import WeeklyReportRun

    async with session_factory() as session:
        session.add(WeeklyReportRun(week_start=week_start, week_end=week_end,
                                    status=status, wecom_sent=wecom_sent))
        await session.commit()


class TestWeeklyReportDue:
    def test_not_due_before_data_lag_clears(self, async_session_factory):
        from datetime import date

        # Sunday just ended yesterday — WeChat's 1-2 day lag hasn't cleared.
        today = date(2026, 9, 14)  # Monday; week_end = Sunday 9/13, only 1 day ago
        settings = _FakeSchedulerSettings()
        due = asyncio.run(
            scheduler_mod._weekly_report_due(settings, today, async_session_factory=async_session_factory)
        )
        assert due is False

    def test_due_when_no_report_exists_yet(self, async_session_factory):
        from datetime import date

        today = date(2026, 9, 15)  # 2 days after week_end (9/13) — lag cleared
        settings = _FakeSchedulerSettings()
        due = asyncio.run(
            scheduler_mod._weekly_report_due(settings, today, async_session_factory=async_session_factory)
        )
        assert due is True

    def test_not_due_after_success(self, async_session_factory):
        from datetime import date

        asyncio.run(
            _insert_weekly_report_run(
                async_session_factory, week_start=date(2026, 9, 7), week_end=date(2026, 9, 13),
                status="success", wecom_sent=True,
            )
        )
        today = date(2026, 9, 15)
        settings = _FakeSchedulerSettings()
        due = asyncio.run(
            scheduler_mod._weekly_report_due(settings, today, async_session_factory=async_session_factory)
        )
        assert due is False

    def test_still_due_after_a_prior_error(self, async_session_factory):
        """A failed run must not permanently suppress the week — only a
        successful run should satisfy the gate."""
        from datetime import date

        asyncio.run(
            _insert_weekly_report_run(
                async_session_factory, week_start=date(2026, 9, 7), week_end=date(2026, 9, 13), status="error"
            )
        )
        today = date(2026, 9, 15)
        settings = _FakeSchedulerSettings()
        due = asyncio.run(
            scheduler_mod._weekly_report_due(settings, today, async_session_factory=async_session_factory)
        )
        assert due is True


# ── weekly_report_loop: startup timing + missed-week alert ──────────────────

class _StopLoop(Exception):
    pass


def _drive_weekly_loop(monkeypatch, *, now, sleeps_before_stop=1, due=True, missed=None):
    """Run weekly_report_loop with a fake clock/sleep; return (events, alerts)."""
    from datetime import date

    events: list[str] = []
    alerts: list[str] = []
    sleeps = {"n": 0}

    async def fake_due(settings, today):
        events.append(f"due:{today}")
        return due

    async def fake_run():
        events.append("run")

    async def fake_missed(this_week_start, **kw):
        return list(missed or [])

    async def fake_notify(text):
        alerts.append(text)
        return True

    async def fake_sleep(delay):
        events.append("sleep")
        sleeps["n"] += 1
        if sleeps["n"] >= sleeps_before_stop:
            raise _StopLoop

    monkeypatch.setattr(scheduler_mod, "_weekly_report_due", fake_due)
    monkeypatch.setattr(scheduler_mod, "_run_weekly_report_once", fake_run)
    monkeypatch.setattr(scheduler_mod, "_missed_report_weeks", fake_missed)
    monkeypatch.setattr(scheduler_mod, "_notify_wecom", fake_notify)
    settings = _FakeSchedulerSettings(report_hour=9)
    with pytest.raises(_StopLoop):
        asyncio.run(scheduler_mod.weekly_report_loop(settings, now_fn=lambda: now, sleep_fn=fake_sleep))
    return events, alerts


class TestWeeklyReportLoop:
    def test_restart_before_report_hour_sleeps_first(self, monkeypatch):
        """A midnight restart on a Tuesday must not push the overdue report
        immediately — it waits for report_hour."""
        from zoneinfo import ZoneInfo

        now = datetime(2026, 9, 15, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        events, _ = _drive_weekly_loop(monkeypatch, now=now)
        assert events == ["sleep"]

    def test_restart_after_report_hour_checks_immediately(self, monkeypatch):
        from zoneinfo import ZoneInfo

        now = datetime(2026, 9, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        events, _ = _drive_weekly_loop(monkeypatch, now=now)
        assert events == ["due:2026-09-15", "run", "sleep"]

    def test_after_sleeping_the_daily_check_runs(self, monkeypatch):
        from zoneinfo import ZoneInfo

        now = datetime(2026, 9, 15, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        events, _ = _drive_weekly_loop(monkeypatch, now=now, sleeps_before_stop=2)
        assert events == ["sleep", "due:2026-09-15", "run", "sleep"]

    def test_missed_weeks_alert_once_and_are_not_backfilled(self, monkeypatch):
        from datetime import date
        from zoneinfo import ZoneInfo

        now = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        missed = [date(2026, 9, 7), date(2026, 9, 14)]
        events, alerts = _drive_weekly_loop(monkeypatch, now=now, sleeps_before_stop=2, missed=missed)
        assert events.count("run") == 2  # only the current week, re-checked daily
        assert len(alerts) == 1
        assert "2026-09-07" in alerts[0] and "2026-09-14" in alerts[0]
        assert "不会自动补发" in alerts[0]

    def test_not_due_means_no_run_and_no_alert(self, monkeypatch):
        from zoneinfo import ZoneInfo

        now = datetime(2026, 9, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        events, alerts = _drive_weekly_loop(monkeypatch, now=now, due=False, missed=["x"])
        assert "run" not in events and alerts == []


class TestMissedReportWeeks:
    def test_lists_gap_between_last_delivered_and_current_week(self, async_session_factory):
        from datetime import date

        asyncio.run(_insert_weekly_report_run(
            async_session_factory, week_start=date(2026, 8, 31), week_end=date(2026, 9, 6),
            status="success", wecom_sent=True,
        ))
        asyncio.run(_insert_weekly_report_run(
            async_session_factory, week_start=date(2026, 9, 7), week_end=date(2026, 9, 13), status="error",
        ))
        missed = asyncio.run(scheduler_mod._missed_report_weeks(
            date(2026, 9, 21), async_session_factory=async_session_factory
        ))
        assert missed == [date(2026, 9, 7), date(2026, 9, 14)]

    def test_no_history_means_nothing_missed(self, async_session_factory):
        from datetime import date

        assert asyncio.run(scheduler_mod._missed_report_weeks(
            date(2026, 9, 21), async_session_factory=async_session_factory
        )) == []

    def test_consecutive_weeks_have_no_gap(self, async_session_factory):
        from datetime import date

        asyncio.run(_insert_weekly_report_run(
            async_session_factory, week_start=date(2026, 9, 7), week_end=date(2026, 9, 13),
            status="success", wecom_sent=True,
        ))
        assert asyncio.run(scheduler_mod._missed_report_weeks(
            date(2026, 9, 14), async_session_factory=async_session_factory
        )) == []


# ── RestartTracker: supervise() crash-loop alerting ─────────────────────────

class TestRestartTracker:
    def test_alerts_on_third_restart_within_window_then_rate_limits(self):
        clock = {"t": 0.0}
        tracker = scheduler_mod.RestartTracker(threshold=3, window_seconds=600, clock=lambda: clock["t"])
        assert tracker.record("watchdog", RuntimeError("a")) is None
        clock["t"] = 30
        assert tracker.record("watchdog", RuntimeError("b")) is None
        clock["t"] = 60
        alert = tracker.record("watchdog", RuntimeError("boom"))
        assert alert and "watchdog" in alert and "3 次" in alert and "boom" in alert
        clock["t"] = 90
        assert tracker.record("watchdog") is None  # rate-limited
        clock["t"] = 700  # window since last alert elapsed and still crash-looping
        tracker.record("watchdog")
        clock["t"] = 715
        tracker.record("watchdog")
        clock["t"] = 730
        assert tracker.record("watchdog") is not None

    def test_restarts_spread_over_more_than_window_do_not_alert(self):
        clock = {"t": 0.0}
        tracker = scheduler_mod.RestartTracker(clock=lambda: clock["t"])
        for t in (0, 400, 800, 1200):
            clock["t"] = t
            assert tracker.record("backup") is None

    def test_tasks_are_tracked_independently(self):
        tracker = scheduler_mod.RestartTracker(clock=lambda: 0.0)
        tracker.record("a")
        tracker.record("a")
        assert tracker.record("b") is None
        assert tracker.record("a") is not None

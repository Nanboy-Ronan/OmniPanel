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
    rpa_backup_dir: str = "unused"


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


class TestRunWatchdogChecksCollector:
    def test_disabled_skips_check(self, sync_session_factory):
        settings = _FakeSchedulerSettings(collector_enabled=False)
        problems = asyncio.run(
            run_watchdog_checks(settings, sync_session_factory=sync_session_factory)
        )
        assert problems == []

    def test_no_runs_ever_reports_problem(self, sync_session_factory):
        settings = _FakeSchedulerSettings(collector_enabled=True)
        problems = asyncio.run(
            run_watchdog_checks(settings, sync_session_factory=sync_session_factory)
        )
        assert len(problems) == 1
        assert "采集器" in problems[0]
        assert "从未" in problems[0]

    def test_recent_run_is_healthy(self, sync_session_factory):
        from app.db.models import CollectorRun

        with sync_session_factory() as session:
            session.add(CollectorRun(platform="xhs", account_id=1, status="success"))
            session.commit()

        settings = _FakeSchedulerSettings(collector_enabled=True)
        problems = asyncio.run(
            run_watchdog_checks(settings, sync_session_factory=sync_session_factory)
        )
        assert problems == []

    def test_stale_run_reports_problem(self, sync_session_factory):
        from app.db.models import CollectorRun

        with sync_session_factory() as session:
            run = CollectorRun(platform="xhs", account_id=1, status="success")
            session.add(run)
            session.commit()
            run_id = run.id
        # 72h ago, computed via the DB's own now() so it can't drift from
        # whatever Postgres session timezone this engine happens to use.
        _age_collector_run(sync_session_factory, run_id, hours_ago=72)

        settings = _FakeSchedulerSettings(collector_enabled=True, watchdog_max_age_hours=30)
        problems = asyncio.run(
            run_watchdog_checks(settings, sync_session_factory=sync_session_factory)
        )
        assert len(problems) == 1
        assert "采集器" in problems[0]
        assert "30 小时" in problems[0]


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
        settings = _FakeSchedulerSettings(rap_disable_monthly_backup=True, rpa_backup_dir=str(tmp_path))
        problems = asyncio.run(run_watchdog_checks(settings))
        assert problems == []

    def test_missing_stamp_reports_problem(self, tmp_path):
        settings = _FakeSchedulerSettings(rap_disable_monthly_backup=False, rpa_backup_dir=str(tmp_path))
        problems = asyncio.run(run_watchdog_checks(settings))
        assert len(problems) == 1
        assert "备份" in problems[0]
        assert "从未" in problems[0]

    def test_recent_stamp_is_healthy(self, tmp_path):
        (tmp_path / ".last_monthly_backup").write_text(datetime.now().isoformat())
        settings = _FakeSchedulerSettings(rap_disable_monthly_backup=False, rpa_backup_dir=str(tmp_path))
        problems = asyncio.run(run_watchdog_checks(settings))
        assert problems == []

    def test_stale_stamp_reports_problem(self, tmp_path):
        from datetime import timedelta

        stale = datetime.now() - timedelta(days=40)
        (tmp_path / ".last_monthly_backup").write_text(stale.isoformat())
        settings = _FakeSchedulerSettings(
            rap_disable_monthly_backup=False, watchdog_backup_max_age_days=35, rpa_backup_dir=str(tmp_path)
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
        import app.views.media.routes as routes_mod

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
        import app.views.media.routes as routes_mod

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
        import app.views.media.routes as routes_mod

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
        import app.views.media.routes as routes_mod

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
        import app.views.media.routes as routes_mod

        async def fake_ensure(session):
            return []

        monkeypatch.setattr(routes_mod, "_ensure_env_wechat_accounts", fake_ensure)

        settings = _FakeSchedulerSettings()
        asyncio.run(scheduler_mod._run_wechat_sync_once(settings))

        assert wecom_sent == []

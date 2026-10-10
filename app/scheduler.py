"""Background scheduler for automated WeChat metric syncs.

The only entry point for external callers is ``wechat_auto_sync_loop``, which
is started as an asyncio task from the FastAPI lifespan when
``WECHAT_AUTO_SYNC_ENABLED=true``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .utils.wecom_bot import send_wecom_alert

logger = logging.getLogger(__name__)

_FALLBACK_TZ = "UTC"


def seconds_until_next_run(hour: int, tz_name: str) -> float:
    """Return wall-clock seconds until the next *hour*:00:00 in *tz_name*.

    If *tz_name* is not a valid IANA timezone the function falls back to UTC
    and logs a warning rather than raising.
    """
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r; falling back to UTC for auto-sync schedule", tz_name)
        tz = ZoneInfo(_FALLBACK_TZ)

    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _notify_wecom(text: str) -> bool:
    """Send a WeCom alert without blocking the event loop.

    send_wecom_alert() is synchronous httpx and never raises; it returns
    False when the alert was not delivered (unconfigured, no recipient,
    rejected by WeCom). That is surfaced here at ERROR with the alert text so
    an undelivered alert still leaves a trace in the journal.
    """
    sent = await asyncio.to_thread(send_wecom_alert, text)
    if not sent:
        logger.error("WeCom alert NOT delivered: %s", text[:500])
    return bool(sent)


async def monthly_backup_loop(settings) -> None:  # type: ignore[type-arg]
    """Infinite background loop: check and run a monthly database backup.

    Runs ``monthly_backup()`` once at startup (so a missed backup is caught
    immediately on restart), then wakes daily at ``settings.backup_hour`` in
    ``settings.app_timezone`` and checks again.  The 30-day gate inside
    ``monthly_backup()`` ensures the actual ``pg_dump`` only fires when enough
    time has elapsed — running the check daily is safe and harmless.
    """
    from .db.backup import monthly_backup

    hour = settings.backup_hour
    tz_name = settings.app_timezone

    logger.info("Monthly backup loop started — daily check at %02d:00 %s", hour, tz_name)

    # Run immediately at startup so any overdue backup is caught on restart.
    try:
        path = await asyncio.to_thread(monthly_backup)
        if path:
            logger.info("Monthly backup written: %s", path)
        else:
            logger.info("Monthly backup: not due yet — skipping startup run")
    except Exception as exc:
        logger.error("Monthly backup startup run failed: %s", exc, exc_info=True)

    while True:
        delay = seconds_until_next_run(hour, tz_name)
        logger.info("Monthly backup: next check in %.0f s (%.1f h)", delay, delay / 3600)
        await asyncio.sleep(delay)

        try:
            path = await asyncio.to_thread(monthly_backup)
            if path:
                logger.info("Monthly backup written: %s", path)
            else:
                logger.info("Monthly backup: not due yet — skipping")
        except asyncio.CancelledError:
            logger.info("Monthly backup loop cancelled — shutting down")
            raise
        except Exception as exc:
            logger.error("Monthly backup failed: %s", exc, exc_info=True)


async def daily_backup_loop(settings) -> None:  # type: ignore[type-arg]
    from .db.backup import daily_backup

    while True:
        try:
            path = await asyncio.to_thread(daily_backup)
            if path:
                logger.info("Daily backup written: %s", path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily backup failed")
        await asyncio.sleep(seconds_until_next_run(settings.backup_hour, settings.app_timezone))


async def _run_wechat_sync_once(settings) -> None:  # type: ignore[type-arg]
    """One pass of the WeChat auto-sync: sync every configured account, then
    send exactly one WeCom notification for the whole pass — success or
    failure, so a completed run is never silent either way.

    Split out of ``wechat_auto_sync_loop`` so it's independently callable/
    testable without also driving the loop's sleep-until-next-run timing
    (mirrors ``app.collector.runner.run_collect`` being the tested unit while
    the collector's own scheduling lives in a systemd timer, not Python).

    Errors for individual accounts are logged and skipped so a single
    failing account does not abort the rest of the run.
    """
    from .services.wechat import (
        _ensure_env_wechat_accounts, _sync_one_wechat_account, _record_failed_wechat_sync,
    )
    from .db import AsyncSessionLocal

    today = date.today()
    # WeChat DataCube has a 1-2 day processing lag; cap end_date to 2 days ago
    # so we never request data that hasn't been computed yet (errcode 61501).
    end_date = today - timedelta(days=2)
    start_date = today - timedelta(days=settings.wechat_auto_sync_window_days)

    logger.info("WeChat auto-sync starting: %s → %s", start_date, end_date)
    ok_lines: list[str] = []
    failed_lines: list[str] = []
    try:
        async with AsyncSessionLocal() as session:
            accounts = await _ensure_env_wechat_accounts(session)
            if not accounts:
                logger.warning("WeChat auto-sync: no accounts configured — skipping run")
                return
            await session.commit()

        for account in accounts:
            async with AsyncSessionLocal() as session:
                try:
                    result = await _sync_one_wechat_account(
                        session, account, start_date, end_date
                    )
                    await session.commit()
                    logger.info(
                        "WeChat auto-sync finished: account=%s posts=%s metrics=%s",
                        account.name,
                        result.get("posts_upserted"),
                        result.get("metrics_upserted"),
                    )
                    ok_lines.append(
                        f"{account.name}: posts={result.get('posts_upserted', 0)} "
                        f"metrics={result.get('metrics_upserted', 0)}"
                    )
                except Exception as exc:
                    await session.rollback()
                    logger.error(
                        "WeChat auto-sync failed for account=%s: %s",
                        account.name,
                        exc,
                        exc_info=True,
                    )
                    failed_lines.append(f"{account.name}: {exc}")
                    if getattr(account, "id", None) is not None:
                        try:
                            await _record_failed_wechat_sync(
                                session, account.id, start_date, end_date, exc
                            )
                        except Exception:
                            logger.exception("Could not record failed WeChat sync for account=%s", account.id)
                            await session.rollback()

        if failed_lines:
            header = (
                f"[微信同步告警] {start_date} → {end_date}，"
                f"{len(failed_lines)} 个账号失败：\n"
            )
            lines = [f"- {f}" for f in failed_lines]
            if ok_lines:
                lines.append("")
                lines.append("成功的账号：")
                lines.extend(f"- {o}" for o in ok_lines)
            await _notify_wecom(header + "\n".join(lines))
        elif ok_lines and settings.wecom_notify_success:
            header = f"[微信同步] 每日同步成功（{start_date} → {end_date}）：\n"
            await _notify_wecom(header + "\n".join(f"- {o}" for o in ok_lines))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("WeChat auto-sync session error: %s", exc, exc_info=True)
        await _notify_wecom(
            f"[微信同步告警] {start_date} → {end_date}，运行异常：{exc}"
        )


async def wechat_auto_sync_loop(settings) -> None:  # type: ignore[type-arg]
    """Infinite background loop: sync WeChat metrics once per day.

    Sleeps until ``settings.wechat_auto_sync_hour`` in ``settings.app_timezone``,
    then runs ``_run_wechat_sync_once`` to sync all configured WeChat Official
    Accounts, covering articles published within the last
    ``settings.wechat_auto_sync_window_days`` days.

    WeChat's DataCube API retains per-article statistics for approximately
    180 days from each article's publish date.  By running daily with a
    170-day look-back window we capture every metric before it expires,
    with a 10-day safety buffer.

    The loop never exits; it is cancelled only when the FastAPI application
    shuts down.
    """
    window_days = settings.wechat_auto_sync_window_days
    hour = settings.wechat_auto_sync_hour
    tz_name = settings.app_timezone

    logger.info(
        "WeChat auto-sync enabled — window=%d days, runs daily at %02d:00 %s",
        window_days,
        hour,
        tz_name,
    )

    while True:
        delay = seconds_until_next_run(hour, tz_name)
        logger.info(
            "WeChat auto-sync: next run in %.0f s (%.1f h)", delay, delay / 3600
        )
        await asyncio.sleep(delay)

        try:
            await _run_wechat_sync_once(settings)
        except asyncio.CancelledError:
            logger.info("WeChat auto-sync loop cancelled — shutting down")
            raise
        except Exception as exc:
            # _run_wechat_sync_once already logs+notifies its own failures;
            # this is just a last-resort guard so one bad iteration can't
            # kill the loop.
            logger.error("WeChat auto-sync iteration failed: %s", exc, exc_info=True)


_ZHIHU_LABELS = {"article": "文章", "qa": "问答"}

# A collector run still 'running' after this long was killed (systemd's
# TimeoutStartSec=900, OOM, reboot) before it could record its outcome.
STALE_RUNNING_MINUTES = 30


def _expected_collector_sources(session, settings) -> list[tuple[str, int | None, str | None, str]]:
    """Every (platform, account_id, content_type, label) the collector is
    *supposed* to produce, mirroring app.collector.runner.build_targets but
    read straight from the DB (the watchdog has no API client).

    Intentionally-disabled sources are skipped: 视频号 unless
    COLLECTOR_CHANNELS_ENABLED, 蒲公英 only for accounts with pgy_enabled,
    数据概览 only with COLLECTOR_XHS_OVERVIEW_ENABLED, 京东 only with
    COLLECTOR_JD_ENABLED.
    """
    from sqlalchemy import select

    from .db.models import WxChannelsAccount, XhsAccount

    sources: list[tuple[str, int | None, str | None, str]] = []
    xhs_enabled = getattr(settings, "collector_xhs_enabled", True)
    pgy_enabled = getattr(settings, "collector_pugongying_enabled", True)
    if xhs_enabled or pgy_enabled:
        accounts = session.execute(
            select(XhsAccount).where(XhsAccount.is_active.is_(True)).order_by(XhsAccount.id)
        ).scalars().all()
        for acc in accounts:
            if xhs_enabled:
                sources.append(("xhs", acc.id, None, f"小红书·{acc.name}"))
                if getattr(settings, "collector_xhs_overview_enabled", False):
                    sources.append(("xhs", acc.id, "overview", f"小红书数据概览·{acc.name}"))
            if pgy_enabled and acc.pgy_enabled:
                sources.append(("pugongying", acc.id, None, f"蒲公英·{acc.name}"))
    if getattr(settings, "collector_zhihu_enabled", True):
        for content_type in ("article", "qa"):
            sources.append(("zhihu", None, content_type, f"知乎·{_ZHIHU_LABELS[content_type]}"))
    if getattr(settings, "collector_channels_enabled", False):
        for acc in session.execute(
            select(WxChannelsAccount).where(WxChannelsAccount.is_active.is_(True)).order_by(WxChannelsAccount.id)
        ).scalars().all():
            sources.append(("channels", acc.id, None, f"视频号·{acc.name}"))
    if getattr(settings, "collector_jd_enabled", False):
        sources.append(("jd", None, None, "京东订单"))
    return sources


def _collector_source_problems_sync(session_factory, settings, max_age_hours: float) -> list[str]:
    """Per-source staleness of the *latest successful collect* run.

    Verify runs (triggered_by='verify') only check a login session, so they
    never count as fresh data, and failed runs don't either. Previously the
    check used max(started_at) over every row, so an evening verify masked a
    failed 06:30 collect and one dead platform never alerted while any other
    platform was still running. The age is computed by Postgres (now() -
    max(started_at)) so it is immune to app/DB clock differences.
    """
    from sqlalchemy import extract, func, select

    from .db.models import CollectorRun

    with session_factory() as session:
        expected = _expected_collector_sources(session, settings)
        if not expected:
            return []
        rows = session.execute(
            select(
                CollectorRun.platform,
                CollectorRun.account_id,
                CollectorRun.content_type,
                extract("epoch", func.now() - func.max(CollectorRun.started_at)),
            )
            .where(CollectorRun.status == "success", CollectorRun.triggered_by != "verify")
            .group_by(CollectorRun.platform, CollectorRun.account_id, CollectorRun.content_type)
        ).all()
    ages = {(r[0], r[1], r[2]): float(r[3]) for r in rows if r[3] is not None}

    problems = []
    for platform, account_id, content_type, label in expected:
        age = ages.get((platform, account_id, content_type))
        if age is None:
            problems.append(f"采集器·{label}：从未成功采集过")
        elif age > max_age_hours * 3600:
            problems.append(
                f"采集器·{label}：已 {age / 3600:.1f} 小时没有成功采集"
                f"（阈值 {max_age_hours} 小时）"
            )
    return problems


def _reap_stale_collector_runs_sync(session_factory, minutes: int = STALE_RUNNING_MINUTES) -> list[str]:
    """Mark CollectorRun rows stuck in 'running' for > *minutes* as 'killed'
    and describe each one. A run only stays 'running' when the collector
    process died mid-target (systemd timeout, OOM, reboot); without this
    those rows stayed 'running' forever."""
    from sqlalchemy import text

    with session_factory() as session:
        rows = session.execute(
            text(
                "UPDATE collector_runs SET status = 'killed', finished_at = now(), "
                "error_message = COALESCE(error_message, :msg) "
                "WHERE status = 'running' AND started_at < now() - make_interval(mins => :mins) "
                "RETURNING id, platform, account_id, content_type, started_at"
            ),
            {"mins": minutes, "msg": f"进程在记录结果前退出（running 超过 {minutes} 分钟，由健康检查标记）"},
        ).all()
        session.commit()
    problems = []
    for run_id, platform, account_id, content_type, started_at in rows:
        target = platform + (f"#{account_id}" if account_id is not None else "") + (
            f"/{content_type}" if content_type else ""
        )
        problems.append(
            f"采集器：运行记录 #{run_id}（{target}，开始于 {started_at:%Y-%m-%d %H:%M}）"
            f"卡在 running 超过 {minutes} 分钟，进程可能被 systemd 超时杀死，已标记为 killed"
        )
    return problems


async def _table_age_seconds_async(session_factory, model, *, source_filter: str | None = None) -> float | None:
    """Seconds since the most recent ``started_at`` row, computed by Postgres
    itself (``now() - max(started_at)``) — None when the table has no rows.
    For tables written via the async engine (e.g. MediaSyncRun, written from
    wechat_auto_sync_loop).
    """
    from sqlalchemy import extract, func, select

    async with session_factory() as session:
        stmt = select(extract("epoch", func.now() - func.max(model.started_at)))
        if source_filter is not None:
            stmt = stmt.where(model.source == source_filter)
        result = await session.execute(stmt)
        value = result.scalar_one()
        return float(value) if value is not None else None


async def run_watchdog_checks(
    settings,
    *,
    sync_session_factory=None,
    async_session_factory=None,
) -> list[str]:
    """Check whether each enabled background pipeline has run recently.

    Per-run success/failure notifications (collector, WeChat auto-sync) only
    fire when a run actually happens — they say nothing if a pipeline stops
    running altogether (disabled timer, crashed process, tampered VM). This
    is the daily backstop for that gap.

    CollectorRun is read via the sync engine (the same one app.collector.runs
    writes through) and MediaSyncRun via the async engine (the same one
    wechat_auto_sync_loop writes through). Both engines use the APP_TIMEZONE
    session timezone (see app/db/__init__.py), and every age is computed by
    Postgres as ``now() - max(started_at)``.

    Returns a list of human-readable problem descriptions (empty when every
    enabled pipeline is healthy). Never raises: an unreadable table is
    reported as a problem, not an exception, so one broken check doesn't hide
    the others.
    """
    from .db.models import MediaSyncRun

    problems: list[str] = []
    max_age_hours = settings.watchdog_max_age_hours

    if settings.collector_enabled:
        if sync_session_factory is None:
            from .db import SyncSessionLocal as sync_session_factory  # type: ignore
        try:
            problems.extend(
                await asyncio.to_thread(_reap_stale_collector_runs_sync, sync_session_factory)
            )
        except Exception as exc:
            problems.append(f"采集器：清理卡住的运行记录失败 — {exc}")
        try:
            problems.extend(
                await asyncio.to_thread(
                    _collector_source_problems_sync, sync_session_factory, settings, max_age_hours
                )
            )
        except Exception as exc:
            problems.append(f"采集器：健康检查失败 — {exc}")

    if settings.wechat_auto_sync_enabled:
        if async_session_factory is None:
            from .db import AsyncSessionLocal as async_session_factory  # type: ignore
        try:
            age = await _table_age_seconds_async(async_session_factory, MediaSyncRun, source_filter="api")
        except Exception as exc:
            problems.append(f"微信自动同步：健康检查失败 — {exc}")
        else:
            if age is None:
                problems.append("微信自动同步：从未记录过任何运行")
            elif age > max_age_hours * 3600:
                problems.append(
                    f"微信自动同步：已 {age / 3600:.1f} 小时没有运行记录"
                    f"（阈值 {max_age_hours} 小时）"
                )

    if not settings.rap_disable_monthly_backup:
        from .db.backup import _read_last_backup

        try:
            last = await asyncio.to_thread(_read_last_backup, Path(settings.backup_dir))
        except Exception as exc:
            problems.append(f"数据库备份：健康检查失败 — {exc}")
        else:
            max_age_days = settings.watchdog_backup_max_age_days
            if last is None:
                problems.append("数据库备份：从未成功备份过")
            else:
                age_days = (datetime.now() - last).days
                if age_days > max_age_days:
                    problems.append(
                        f"数据库备份：已 {age_days} 天没有成功备份（阈值 {max_age_days} 天）"
                    )
        daily_stamp = Path(settings.backup_dir) / ".last_daily_backup"
        try:
            daily_age = (datetime.now() - datetime.fromisoformat(daily_stamp.read_text().strip())).days
        except (OSError, ValueError):
            problems.append("每日数据库备份：从未成功备份过")
        else:
            if daily_age > settings.watchdog_daily_backup_max_age_days:
                problems.append(f"每日数据库备份：已 {daily_age} 天没有成功备份")
        # A fresh stamp only proves pg_dump exited 0; check the newest dump
        # is non-empty and really starts with a pg_dump header.
        from .db.backup import latest_dump, validate_dump

        try:
            dump = await asyncio.to_thread(latest_dump, settings.backup_dir)
            dump_problem = (
                "备份目录中没有任何备份文件" if dump is None
                else await asyncio.to_thread(validate_dump, dump)
            )
        except Exception as exc:
            dump_problem = f"检查备份文件失败 — {exc}"
        if dump_problem:
            problems.append(f"数据库备份文件：{dump_problem}")

    return problems


async def watchdog_loop(settings) -> None:  # type: ignore[type-arg]
    """Infinite background loop: daily check that every enabled background
    pipeline (collector, WeChat auto-sync, monthly backup) actually ran
    recently. Sends a WeCom alert only when something looks unhealthy — a
    daily "everything is fine" message would just be noise on top of the
    per-run success notifications those pipelines already send.
    """
    hour = settings.watchdog_hour
    tz_name = settings.app_timezone

    logger.info("Pipeline watchdog enabled — daily check at %02d:00 %s", hour, tz_name)

    while True:
        delay = seconds_until_next_run(hour, tz_name)
        logger.info("Watchdog: next check in %.0f s (%.1f h)", delay, delay / 3600)
        await asyncio.sleep(delay)

        try:
            problems = await run_watchdog_checks(settings)
            if problems:
                logger.warning("Watchdog: %d pipeline(s) unhealthy: %s", len(problems), problems)
                text = "[健康检查告警] 数据管道异常：\n" + "\n".join(f"- {p}" for p in problems)
                await _notify_wecom(text)
            else:
                logger.info("Watchdog: all pipelines healthy")
        except asyncio.CancelledError:
            logger.info("Watchdog loop cancelled — shutting down")
            raise
        except Exception as exc:
            logger.error("Watchdog check failed: %s", exc, exc_info=True)


async def _weekly_report_due(settings, today: date, *, async_session_factory=None) -> bool:  # type: ignore[type-arg]
    """True when the most recently completed ISO week needs a report or push,
    and WeChat's 1-2 day data lag has had time to clear.

    A successful report whose notification failed remains due so the next
    day's check retries it (generate_weekly_report upserts).

    ``async_session_factory`` defaults to the real app session (like
    ``run_watchdog_checks``'s equivalent parameter) but accepts an override
    for tests against an isolated database.
    """
    from sqlalchemy import select

    from .db import AsyncSessionLocal
    from .db.models import WeeklyReportRun
    from .reports.weekly_media import is_week_due, week_bounds

    session_factory = async_session_factory or AsyncSessionLocal

    bounds = week_bounds(today)
    if not is_week_due(bounds.this_week_end, today):
        return False

    async with session_factory() as session:
        result = await session.execute(
            select(WeeklyReportRun.id).where(
                WeeklyReportRun.week_start == bounds.this_week_start,
                WeeklyReportRun.status == "success",
                WeeklyReportRun.wecom_sent.is_(True),
            )
        )
        return result.scalar_one_or_none() is None


async def _run_weekly_report_once() -> None:
    """One weekly-report generation pass — split out for direct testability,
    mirroring _run_wechat_sync_once."""
    from .db import AsyncSessionLocal
    from .reports.service import generate_weekly_report

    async with AsyncSessionLocal() as session:
        run = await generate_weekly_report(session)
        logger.info("Weekly report: week=%s status=%s wecom_sent=%s", run.week_start, run.status, run.wecom_sent)


async def _missed_report_weeks(this_week_start: date, *, async_session_factory=None) -> list[date]:
    """Week starts strictly between the newest delivered report and
    *this_week_start* that never got a delivered report.

    The loop only ever generates the most recently completed week, so if the
    leader was down for more than a week the weeks in between are silently
    skipped. They are not backfilled automatically (an old week's numbers
    would be pushed as if new) — this only lists them for an alert. Empty
    when no report was ever delivered (fresh deploy: nothing was "missed").
    """
    from sqlalchemy import func, select

    from .db import AsyncSessionLocal
    from .db.models import WeeklyReportRun

    session_factory = async_session_factory or AsyncSessionLocal
    async with session_factory() as session:
        delivered = set(
            (
                await session.execute(
                    select(WeeklyReportRun.week_start).where(
                        WeeklyReportRun.status == "success",
                        WeeklyReportRun.wecom_sent.is_(True),
                        WeeklyReportRun.week_start < this_week_start,
                    )
                )
            ).scalars().all()
        )
    if not delivered:
        return []
    week = max(delivered) + timedelta(days=7)
    missed = []
    while week < this_week_start:
        if week not in delivered:
            missed.append(week)
        week += timedelta(days=7)
    return missed


def _local_now(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        return datetime.now(ZoneInfo(_FALLBACK_TZ))


async def _weekly_report_check(settings, today: date, alerted_missed: set[date]) -> None:
    """One due-check: alert about skipped weeks (once each), then generate
    the most recently completed week if it is due."""
    from .reports.weekly_media import week_bounds

    if not await _weekly_report_due(settings, today):
        return
    this_week_start = week_bounds(today).this_week_start
    try:
        missed = [w for w in await _missed_report_weeks(this_week_start) if w not in alerted_missed]
    except Exception:
        logger.exception("Weekly report: could not check for missed weeks")
        missed = []
    if missed:
        weeks = "、".join(f"{w:%Y-%m-%d}~{w + timedelta(days=6):%m-%d}" for w in missed)
        await _notify_wecom(
            f"[周报告警] 以下 {len(missed)} 周的周报未生成/未推送（服务停机超过一周）：{weeks}。"
            "系统不会自动补发旧周报，如需补发请在管理页手动生成。"
        )
        alerted_missed.update(missed)
    await _run_weekly_report_once()


async def weekly_report_loop(settings, *, now_fn=None, sleep_fn=None) -> None:  # type: ignore[type-arg]
    """Infinite background loop: check daily at ``settings.report_hour`` and
    generate the weekly 公众号+小红书 report once the most recently completed
    week is due.

    The due gate is calendar-week + data-lag based (see _weekly_report_due),
    so a restart can't double-send. On startup the check only runs
    immediately if the local hour is already >= report_hour; a restart
    earlier in the day (e.g. a midnight deploy) sleeps until report_hour
    instead of pushing an overdue report in the middle of the night.

    ``now_fn``/``sleep_fn`` are injection points for loop-level tests.
    """
    hour = settings.report_hour
    tz_name = settings.app_timezone
    now_fn = now_fn or (lambda: _local_now(tz_name))
    sleep_fn = sleep_fn or asyncio.sleep
    alerted_missed: set[date] = set()
    logger.info("Weekly report loop started — daily check at %02d:00 %s", hour, tz_name)

    run_now = now_fn().hour >= hour
    while True:
        if run_now:
            try:
                await _weekly_report_check(settings, now_fn().date(), alerted_missed)
            except asyncio.CancelledError:
                logger.info("Weekly report loop cancelled — shutting down")
                raise
            except Exception as exc:
                logger.error("Weekly report generation failed: %s", exc, exc_info=True)
                await _notify_wecom(f"[周报告警] 生成失败：{exc}")
        run_now = True

        delay = seconds_until_next_run(hour, tz_name)
        logger.info("Weekly report: next check in %.0f s (%.1f h)", delay, delay / 3600)
        await sleep_fn(delay)


class RestartTracker:
    """Detects a background task that keeps crashing.

    ``supervise()`` in app/main.py restarts a crashed loop after 30s, which
    used to be completely silent. ``record(name)`` returns an alert text when
    *name* has restarted ``threshold`` times within ``window_seconds`` — at
    most once per ``window_seconds`` per task, so a crash-looping task does
    not page every 30 seconds.
    """

    def __init__(self, threshold: int = 3, window_seconds: float = 600, clock=None):
        self.threshold = threshold
        self.window = window_seconds
        self._clock = clock or time.monotonic
        self._restarts: dict[str, deque[float]] = {}
        self._last_alert: dict[str, float] = {}

    def record(self, name: str, error: BaseException | None = None) -> str | None:
        now = self._clock()
        times = self._restarts.setdefault(name, deque())
        times.append(now)
        while times and now - times[0] > self.window:
            times.popleft()
        if len(times) < self.threshold:
            return None
        last = self._last_alert.get(name)
        if last is not None and now - last < self.window:
            return None
        self._last_alert[name] = now
        detail = f"{type(error).__name__}: {error}" if error is not None else "未知错误"
        return (
            f"[后台任务告警] {name} 在 {int(self.window // 60)} 分钟内已崩溃重启 {len(times)} 次，"
            f"最近一次错误：{detail}。请检查 rpa-backend 日志（journalctl -u rpa-backend）。"
        )

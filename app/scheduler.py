"""Background scheduler for automated WeChat metric syncs.

The only entry point for external callers is ``wechat_auto_sync_loop``, which
is started as an asyncio task from the FastAPI lifespan when
``WECHAT_AUTO_SYNC_ENABLED=true``.
"""
from __future__ import annotations

import asyncio
import logging
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


async def _notify_wecom(text: str) -> None:
    """Send a WeCom alert without blocking the event loop.

    send_wecom_alert() is synchronous httpx and already swallows its own
    errors (never raises); this just keeps that call off the asyncio loop.
    """
    await asyncio.to_thread(send_wecom_alert, text)


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
    # Lazy imports to avoid circular dependencies at import time.
    from .views.media.routes import _ensure_env_wechat_accounts, _sync_one_wechat_account
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

            for account in accounts:
                try:
                    result = await _sync_one_wechat_account(
                        session, account, start_date, end_date
                    )
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
                    logger.error(
                        "WeChat auto-sync failed for account=%s: %s",
                        account.name,
                        exc,
                        exc_info=True,
                    )
                    failed_lines.append(f"{account.name}: {exc}")
                    # Roll back any aborted transaction so the session is
                    # clean for the next account — without this a failed
                    # statement poisons every subsequent query in the loop.
                    try:
                        await session.rollback()
                    except Exception:
                        pass

            await session.commit()

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


def _table_age_seconds_sync(session_factory, model) -> float | None:
    """Seconds since the most recent ``started_at`` row, computed by Postgres
    itself (``now() - max(started_at)``) — None when the table has no rows.

    Runs the diff inside the DB rather than comparing against a Python
    timestamp so it's immune to any clock/timezone mismatch between the app
    server and the database.
    """
    from sqlalchemy import extract, func, select

    with session_factory() as session:
        stmt = select(extract("epoch", func.now() - func.max(model.started_at)))
        value = session.execute(stmt).scalar_one()
        return float(value) if value is not None else None


async def _table_age_seconds_async(session_factory, model, *, source_filter: str | None = None) -> float | None:
    """Async counterpart of ``_table_age_seconds_sync`` for tables written via
    the async engine (e.g. MediaSyncRun, written from wechat_auto_sync_loop).
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
    wechat_auto_sync_loop writes through) — each check's ``now() -
    max(started_at)`` diff is computed within the same DB session-timezone
    context the row was written in, so it can't be thrown off by the sync and
    async engines using different Postgres session `timezone` settings (see
    app/db/__init__.py).

    Returns a list of human-readable problem descriptions (empty when every
    enabled pipeline is healthy). Never raises: an unreadable table is
    reported as a problem, not an exception, so one broken check doesn't hide
    the others.
    """
    from .db.models import CollectorRun, MediaSyncRun

    problems: list[str] = []
    max_age_hours = settings.watchdog_max_age_hours

    if settings.collector_enabled:
        if sync_session_factory is None:
            from .db import SyncSessionLocal as sync_session_factory  # type: ignore
        try:
            age = await asyncio.to_thread(_table_age_seconds_sync, sync_session_factory, CollectorRun)
        except Exception as exc:
            problems.append(f"采集器（XHS/知乎）：健康检查失败 — {exc}")
        else:
            if age is None:
                problems.append("采集器（XHS/知乎）：从未记录过任何运行")
            elif age > max_age_hours * 3600:
                problems.append(
                    f"采集器（XHS/知乎）：已 {age / 3600:.1f} 小时没有运行记录"
                    f"（阈值 {max_age_hours} 小时）"
                )

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
            last = await asyncio.to_thread(_read_last_backup, Path(settings.rpa_backup_dir))
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

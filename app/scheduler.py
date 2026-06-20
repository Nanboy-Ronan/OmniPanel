"""Background scheduler for automated WeChat metric syncs.

The only entry point for external callers is ``wechat_auto_sync_loop``, which
is started as an asyncio task from the FastAPI lifespan when
``WECHAT_AUTO_SYNC_ENABLED=true``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


async def wechat_auto_sync_loop(settings) -> None:  # type: ignore[type-arg]
    """Infinite background loop: sync WeChat metrics once per day.

    Sleeps until ``settings.wechat_auto_sync_hour`` in ``settings.app_timezone``,
    then syncs all configured WeChat Official Accounts, covering articles
    published within the last ``settings.wechat_auto_sync_window_days`` days.

    WeChat's DataCube API retains per-article statistics for approximately
    180 days from each article's publish date.  By running daily with a
    170-day look-back window we capture every metric before it expires,
    with a 10-day safety buffer.

    Errors for individual accounts are logged and skipped so a single
    failing account does not abort the entire run.  The loop never exits;
    it is cancelled only when the FastAPI application shuts down.
    """
    # Lazy imports to avoid circular dependencies at import time.
    from .views.media.routes import _ensure_env_wechat_accounts, _sync_one_wechat_account
    from .db import AsyncSessionLocal

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

        today = date.today()
        # WeChat DataCube has a 1-2 day processing lag; cap end_date to 2 days ago
        # so we never request data that hasn't been computed yet (errcode 61501).
        end_date = today - timedelta(days=2)
        start_date = today - timedelta(days=window_days)

        logger.info("WeChat auto-sync starting: %s → %s", start_date, end_date)
        try:
            async with AsyncSessionLocal() as session:
                accounts = await _ensure_env_wechat_accounts(session)
                if not accounts:
                    logger.warning("WeChat auto-sync: no accounts configured — skipping run")
                    continue

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
                    except Exception as exc:
                        logger.error(
                            "WeChat auto-sync failed for account=%s: %s",
                            account.name,
                            exc,
                            exc_info=True,
                        )
                        # Roll back any aborted transaction so the session is
                        # clean for the next account — without this a failed
                        # statement poisons every subsequent query in the loop.
                        try:
                            await session.rollback()
                        except Exception:
                            pass

                await session.commit()
        except asyncio.CancelledError:
            logger.info("WeChat auto-sync loop cancelled — shutting down")
            raise
        except Exception as exc:
            logger.error("WeChat auto-sync session error: %s", exc, exc_info=True)

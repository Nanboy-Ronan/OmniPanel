"""On-demand collect requests: queued by the backend, executed in the collector unit.

The backend only inserts a ``collector_requests`` row (after a successful WorkBuddy
login repair, or an admin's "立即采集"). ``python -m app.collector run-requests``,
run every couple of minutes (cron, systemd timer, ...) with the same environment as
the scheduled collect, runs them one target at a time through ``run_collect``. Nothing here imports
Playwright; the browser only starts inside ``run_collect``.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

_logger = logging.getLogger(__name__)

TRIGGER = "request"  # CollectorRun.triggered_by for these runs; counts as a real collect.
# Older requests are left to the next scheduled collect rather than run late.
MAX_QUEUE_AGE = timedelta(hours=6)
# A request still "running" this long belongs to a unit that was killed.
STALE_RUNNING = timedelta(hours=1)
# Stop picking new requests after this, well inside the unit's TimeoutStartSec=900.
RUN_BUDGET_SECONDS = 600

_PLATFORM_FLAGS = {
    "xhs": "collector_xhs_enabled",
    "pugongying": "collector_pugongying_enabled",
    "channels": "collector_channels_enabled",
    "zhihu": "collector_zhihu_enabled",
    "jd": "collector_jd_enabled",
}


def collect_enabled_for(platform: str, settings) -> bool:
    """Whether a collect of this platform would actually run with these settings."""
    flag = _PLATFORM_FLAGS.get(platform)
    return bool(flag and settings.collector_enabled and getattr(settings, flag, False))


def _now():
    return datetime.now(timezone.utc)


def _claim(session_factory, settings):
    """Return (id, platform, account_id), "skipped", or None when the queue is empty."""
    from sqlalchemy import select, update
    from ..db.models import CollectorRequest

    with session_factory() as session:
        now = _now()
        session.execute(update(CollectorRequest).where(
            CollectorRequest.status == "running", CollectorRequest.started_at < now - STALE_RUNNING,
        ).values(status="failed", finished_at=now))
        row = session.execute(select(CollectorRequest).where(CollectorRequest.status == "queued")
                              .order_by(CollectorRequest.id).with_for_update(skip_locked=True)
                              .limit(1)).scalar_one_or_none()
        if row is None:
            session.commit()
            return None
        if row.created_at < now - MAX_QUEUE_AGE or not collect_enabled_for(row.platform, settings):
            row.status, row.finished_at = "skipped", now
            session.commit()
            return "skipped"
        row.status, row.started_at = "running", now
        claimed = (row.id, row.platform, row.account_id)
        session.commit()
        return claimed


def _finish(session_factory, request_id, exit_code):
    from ..db.models import CollectorRequest

    with session_factory() as session:
        row = session.get(CollectorRequest, request_id)
        if row is not None:
            row.status = "done" if exit_code == 0 else "failed"
            row.exit_code, row.finished_at = exit_code, _now()
            session.commit()


def run_requests(*, settings=None, session_factory=None, runner=None, clock=time.monotonic) -> int:
    """Run queued requests until the queue is empty or the budget is used. 0 = all succeeded."""
    from ..config import settings as default_settings

    settings = settings or default_settings
    if session_factory is None:
        from ..db import SyncSessionLocal as session_factory
    if runner is None:
        from .runner import run_collect as runner
    deadline = clock() + RUN_BUDGET_SECONDS
    worst = 0
    while clock() < deadline:
        claimed = _claim(session_factory, settings)
        if claimed is None:
            break
        if claimed == "skipped":
            continue
        request_id, platform, account_id = claimed
        _logger.info("collector request %s: %s account=%s", request_id, platform, account_id)
        try:
            code = runner(settings=settings, triggered_by=TRIGGER, only_platform=platform,
                          only_account_id=account_id)
        except Exception:
            _logger.exception("collector request %s failed", request_id)
            code = 1
        except BaseException:
            # SIGTERM from systemd (CollectorInterrupted): close the request, then exit.
            _finish(session_factory, request_id, 1)
            raise
        _finish(session_factory, request_id, code)
        worst = max(worst, code)
    return worst

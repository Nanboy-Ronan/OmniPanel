"""CollectorRun bookkeeping — direct DB writes via SyncSessionLocal.

The collector runs as a standalone process on the same host/DB as the API
(see app/scheduler.py's precedent for direct-DB access from a background
job), so run-status is written straight to Postgres rather than inventing a
write API purely for this.
"""
from __future__ import annotations

import datetime as dt


def start_run(
    platform: str,
    *,
    account_id: int | None = None,
    content_type: str | None = None,
    triggered_by: str = "schedule",
) -> int:
    from ..db import SyncSessionLocal
    from ..db.models import CollectorRun

    with SyncSessionLocal() as session:
        run = CollectorRun(
            platform=platform,
            account_id=account_id,
            content_type=content_type,
            status="running",
            triggered_by=triggered_by,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def finish_run(
    run_id: int,
    status: str,
    *,
    rows_upserted: int = 0,
    filename: str | None = None,
    error_message: str | None = None,
) -> None:
    from ..db import SyncSessionLocal
    from ..db.models import CollectorRun

    with SyncSessionLocal() as session:
        run = session.get(CollectorRun, run_id)
        if run is None:
            return
        run.status = status
        run.rows_upserted = rows_upserted
        run.filename = filename
        run.error_message = error_message
        run.finished_at = dt.datetime.now()
        session.commit()

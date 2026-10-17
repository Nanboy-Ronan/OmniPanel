"""Periodic purge of expired connector state. Nothing here is needed after expiry."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, exists, or_, select

from ... import db
from ...db.models import (CollectorRepair, CollectorRequest, IntegrationToken, WorkBuddyOAuthClient as Client,
                          WorkBuddyOAuthGrant as Grant, WorkBuddyOAuthRefresh as Refresh,
                          WorkBuddyOAuthRequest as Authorization)

logger = logging.getLogger(__name__)
# Revoked/expired credentials stay listed for a while so employees see what ended and why.
CREDENTIAL_RETENTION = timedelta(days=30)
SHORT_RETENTION = timedelta(days=1)


async def purge_once(session, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    old, recent = now - CREDENTIAL_RETENTION, now - SHORT_RETENTION
    counts = {}
    # Grants and refresh rows cascade from the credential.
    live_grant = exists().where(Grant.token_id == IntegrationToken.id, Grant.expires_at >= old)
    counts["credentials"] = (await session.execute(delete(IntegrationToken).where(or_(
        IntegrationToken.revoked_at < old,
        and_(IntegrationToken.expires_at < old, ~live_grant),
    )))).rowcount
    counts["refresh"] = (await session.execute(delete(Refresh).where(Refresh.used_at < recent))).rowcount
    counts["requests"] = (await session.execute(delete(Authorization).where(Authorization.expires_at < recent))).rowcount
    # Registration is unauthenticated: clients that never completed a connection are dropped.
    counts["clients"] = (await session.execute(delete(Client).where(
        Client.created_at < recent,
        ~exists().where(Grant.client_id == Client.id),
        ~exists().where(Authorization.client_id == Client.id),
    ))).rowcount
    counts["repairs"] = (await session.execute(delete(CollectorRepair).where(CollectorRepair.expires_at < recent))).rowcount
    counts["collect_requests"] = (await session.execute(delete(CollectorRequest).where(
        CollectorRequest.status.in_(("done", "failed", "skipped")), CollectorRequest.created_at < old))).rowcount
    await session.commit()
    return counts


def purge_repair_candidates(sessions_dir, max_age_seconds=3600, clock=None) -> int:
    """Remove `.repair-*.json` candidates left by a process killed mid-verification."""
    import time
    from pathlib import Path
    clock = clock or time.time
    removed = 0
    directory = Path(sessions_dir)
    if not directory.is_dir():
        return 0
    for path in directory.glob(".repair-*.json*"):
        try:
            if clock() - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            logger.warning("Could not remove stale repair candidate %s", path.name)
    return removed


def _purge_raw_archive() -> int:
    from ...db.raw_archive import purge_expired
    with db.SyncSessionLocal() as session:
        return purge_expired(session)


async def retention_loop(interval_seconds=3600):
    from ...collector.paths import sessions_dir
    while True:
        async with db.AsyncSessionLocal() as session:
            counts = await purge_once(session)
        counts["candidates"] = await asyncio.to_thread(purge_repair_candidates, sessions_dir())
        counts["raw_files"] = await asyncio.to_thread(_purge_raw_archive)
        if any(counts.values()):
            logger.info("Connector retention purge %s", counts)
        await asyncio.sleep(interval_seconds)

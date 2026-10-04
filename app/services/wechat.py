"""WeChat account discovery and per-account sync business operations."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..connectors.wechat_official import WeChatOfficialClient
from ..db.models import MediaAccount, MediaPost, MediaPostMetricDaily, MediaSyncRun

logger = logging.getLogger(__name__)
WECHAT_PLATFORM = "wechat_official"


def _wechat_env_accounts() -> list[dict[str, str]]:
    accounts: list[dict[str, str]] = []

    legacy_app_id = os.getenv("WECHAT_OFFICIAL_APP_ID")
    legacy_secret = os.getenv("WECHAT_OFFICIAL_APP_SECRET")
    if legacy_app_id and legacy_secret:
        accounts.append(
            {
                "name": os.getenv("WECHAT_OFFICIAL_ACCOUNT_NAME", "微信公众号"),
                "app_id": legacy_app_id,
                "app_secret": legacy_secret,
            }
        )

    for idx in range(1, 11):
        app_id = os.getenv(f"WECHAT_APP_ID_{idx}")
        app_secret = os.getenv(f"WECHAT_APP_SECRET_{idx}")
        if not app_id or not app_secret:
            continue
        accounts.append(
            {
                "name": os.getenv(f"WECHAT_ACCOUNT_NAME_{idx}", f"微信公众号 {idx}"),
                "app_id": app_id,
                "app_secret": app_secret,
            }
        )

    deduped: dict[str, dict[str, str]] = {}
    for account in accounts:
        deduped[account["app_id"]] = account
    return list(deduped.values())


async def _ensure_env_wechat_accounts(session: AsyncSession) -> list[MediaAccount]:
    accounts: list[MediaAccount] = []
    for env_account in _wechat_env_accounts():
        app_id = env_account["app_id"]

        result = await session.execute(
            select(MediaAccount).where(
                MediaAccount.platform == WECHAT_PLATFORM,
                MediaAccount.app_id == app_id,
            )
        )
        account = result.scalar_one_or_none()
        if account:
            account.name = env_account["name"] or account.name
            accounts.append(account)
            continue

        account = MediaAccount(
            platform=WECHAT_PLATFORM,
            name=env_account["name"],
            app_id=app_id,
            is_active=True,
        )
        session.add(account)
        await session.flush()
        accounts.append(account)
    return accounts


def _wechat_secret_for_account(account: MediaAccount) -> str | None:
    for env_account in _wechat_env_accounts():
        if account.platform == WECHAT_PLATFORM and account.app_id == env_account["app_id"]:
            return env_account["app_secret"]
    return account.app_secret


async def _sync_one_wechat_account(
    session: AsyncSession,
    account: MediaAccount,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    if account.platform != WECHAT_PLATFORM:
        raise HTTPException(status_code=400, detail="Only wechat_official accounts can be synced here")
    app_secret = _wechat_secret_for_account(account)
    if not account.app_id or not app_secret:
        raise HTTPException(status_code=400, detail="WeChat app_id/app_secret missing for account")

    client = WeChatOfficialClient(account.app_id, app_secret)
    logger.info("WeChat sync start: account=%s range=%s~%s", account.id, start_date, end_date)
    rows = await asyncio.to_thread(client.fetch_article_total_rows, start_date, end_date)
    logger.info("WeChat sync fetched %d rows for account=%s", len(rows), account.id)
    run = MediaSyncRun(
        account_id=account.id,
        status="running",
        start_date=start_date,
        end_date=end_date,
    )
    session.add(run)
    await session.flush()
    posts_seen: set[int] = set()
    metrics_count = 0
    for row in rows:
        if not row.get("external_id") or not row.get("metric_date"):
            continue
        post = await _upsert_post(session, account, row)
        posts_seen.add(post.id)
        await _upsert_metric(session, post, row)
        metrics_count += 1

    run.status = "success"
    run.posts_upserted = len(posts_seen)
    run.metrics_upserted = metrics_count
    # Same server-side clock as MediaSyncRun.started_at's
    # server_default=func.now() — see app/collector/runs.py's identical fix.
    run.finished_at = func.now()
    return {
        "account_id": account.id,
        "account_name": account.name,
        "status": run.status,
        "posts_upserted": run.posts_upserted,
        "metrics_upserted": run.metrics_upserted,
    }


async def _record_failed_wechat_sync(
    session: AsyncSession, account_id: int, start_date: date, end_date: date, error: Exception
) -> None:
    """Persist a failure after the account's partial writes were rolled back."""
    session.add(MediaSyncRun(
        account_id=account_id,
        status="failed",
        start_date=start_date,
        end_date=end_date,
        finished_at=func.now(),
        error_message=str(error)[:500],
    ))
    await session.commit()


async def _upsert_post(
    session: AsyncSession,
    account: MediaAccount,
    row: dict[str, Any],
) -> MediaPost:
    external_id = row["external_id"]
    result = await session.execute(
        select(MediaPost).where(
            MediaPost.account_id == account.id,
            MediaPost.external_id == external_id,
        )
    )
    post = result.scalar_one_or_none()
    if post is None:
        post = MediaPost(
            account_id=account.id,
            platform=account.platform,
            external_id=external_id,
            title=row["title"],
            publish_date=row.get("publish_date"),
            url=row.get("url"),
            author=row.get("author"),
        )
        session.add(post)
        await session.flush()
    else:
        post.title = row["title"] or post.title
        post.publish_date = row.get("publish_date") or post.publish_date
        post.url = row.get("url") or post.url
        post.author = row.get("author") or post.author
    return post


async def _upsert_metric(
    session: AsyncSession,
    post: MediaPost,
    row: dict[str, Any],
) -> None:
    metric_date = row["metric_date"]
    result = await session.execute(
        select(MediaPostMetricDaily).where(
            MediaPostMetricDaily.post_id == post.id,
            MediaPostMetricDaily.metric_date == metric_date,
        )
    )
    metric = result.scalar_one_or_none()
    values = {
        "read_user_count": row.get("read_user_count", 0),
        "share_user_count": row.get("share_user_count", 0),
        "add_to_fav_count": row.get("collection_user", 0),
        "like_user": row.get("like_user"),
        "comment_count": row.get("comment_count"),
        "collection_user": row.get("collection_user"),
        "read_avg_time": row.get("read_avg_time"),
        "read_user_source": row.get("read_user_source"),
        "publish_type": row.get("publish_type"),
        "zaikan_user": row.get("zaikan_user"),
        "read_subscribe_user": row.get("read_subscribe_user"),
        "read_delivery_rate": row.get("read_delivery_rate"),
        "praise_money": row.get("praise_money"),
        "read_jump_position": row.get("read_jump_position"),
        "read_finish_rate": row.get("read_finish_rate"),
        "raw_payload": row.get("raw_payload"),
    }
    if metric is None:
        session.add(MediaPostMetricDaily(post_id=post.id, metric_date=metric_date, **values))
    else:
        for key, value in values.items():
            setattr(metric, key, value)

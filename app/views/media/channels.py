"""API endpoints for WeChat Channels (视频号) accounts, uploads, and post queries.

Column mapping verified live against a real 视频号助手 export — see
app/db/etl/channels.py for the verified header list and metric semantics.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import tempfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_active_user, current_admin_user, current_analyst_user
from ... import db as _db_mod
from ...db import get_session
from ...db.etl.channels import parse_channels_api_json, parse_channels_xlsx, upsert_channels_posts
from ...db.models import WxChannelsAccount, WxChannelsPost
from ...utils.logger import log_operation

router = APIRouter(prefix="/media/channels", tags=["channels"])

_logger = logging.getLogger(__name__)
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


# ── Account CRUD ──────────────────────────────────────────────────────────────

class ChannelsAccountCreate(BaseModel):
    name: str


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_channels_account(
    body: ChannelsAccountCreate,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy.exc import IntegrityError
    acc = WxChannelsAccount(name=body.name.strip())
    session.add(acc)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Account '{body.name.strip()}' already exists")
    await session.refresh(acc)
    return {"id": acc.id, "name": acc.name, "is_active": acc.is_active}


@router.get("/accounts")
async def list_channels_accounts(
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(WxChannelsAccount).order_by(WxChannelsAccount.created_at)
    )).scalars().all()
    return [{"id": a.id, "name": a.name, "is_active": a.is_active} for a in rows]


class ChannelsAccountUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


@router.patch("/accounts/{account_id}")
async def update_channels_account(
    account_id: int,
    body: ChannelsAccountUpdate,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy.exc import IntegrityError
    acc = await session.get(WxChannelsAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if body.name is not None:
        acc.name = body.name.strip()
    if body.is_active is not None:
        acc.is_active = body.is_active
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Account '{body.name.strip()}' already exists")
    await session.refresh(acc)
    return {"id": acc.id, "name": acc.name, "is_active": acc.is_active}


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channels_account(
    account_id: int,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    acc = await session.get(WxChannelsAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Account not found")
    await session.delete(acc)
    await session.commit()


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_channels(
    account_id: int = Form(...),
    file: UploadFile = File(...),
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Upload a 视频号 video-data export and upsert videos for a specific account.

    Accepts either format (see app/db/etl/channels.py for both):
      - .json — the collector's own download_post_data API response
        (full account history, one call — this is what
        app/collector/channels.py produces and uploads automatically).
      - .xlsx/.xls/.csv — what a human gets by manually clicking "下载表格"
        in 视频号助手 (数据中心 → 视频数据 → 单篇视频). Note that button's
        own date-range filter caps what's in the file (defaults to 近7天).

    Dedup key: video_id (see app/db/etl/channels.py). Videos absent from
    this file are left untouched in the DB.
    """
    acc = await session.get(WxChannelsAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"WeChat Channels account {account_id} not found")

    filename = file.filename or "upload.xlsx"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xls", ".xlsx", ".csv", ".json"):
        raise HTTPException(status_code=400, detail="请上传 xlsx、xls、csv 或 json 文件。")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    os.unlink(tmp.name)
                    raise HTTPException(status_code=413, detail="文件过大（上限 50 MB）。")
                tmp.write(chunk)
            tmp_path = tmp.name

        def _process(path: str) -> dict:
            if ext == ".json":
                rows = parse_channels_api_json(Path(path).read_bytes(), account_id)
            else:
                if ext == ".csv":
                    df_raw = pd.read_csv(path, header=None, dtype=str)
                else:
                    df_raw = pd.read_excel(path, header=None, dtype=str)
                rows = parse_channels_xlsx(df_raw, account_id)
            if not rows:
                raise ValueError("文件中未解析到有效行，请确认格式正确（列名可能与预期不符，见 app/db/etl/channels.py）。")
            with _db_mod.SyncSessionLocal() as sync_sess:
                return upsert_channels_posts(rows, account_id, sync_sess)

        result = await asyncio.to_thread(_process, tmp_path)

    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("channels_upload_failed filename=%r account=%d: %s",
                      filename, account_id, exc, exc_info=exc)
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    await log_operation(
        str(_user.id),
        "channels_upload",
        {"filename": filename, "account_id": account_id,
         "total": result["total"], "upserted": result["upserted"]},
        session=session,
    )
    return result


# ── Post listing ──────────────────────────────────────────────────────────────

@router.get("/posts")
async def list_channels_posts(
    account_id: int | None = Query(None),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    limit: int = Query(500, ge=1, le=1000),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(WxChannelsPost).order_by(WxChannelsPost.publish_date.desc()).limit(limit)
    if account_id is not None:
        stmt = stmt.where(WxChannelsPost.account_id == account_id)
    if start_date:
        stmt = stmt.where(WxChannelsPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(WxChannelsPost.publish_date <= end_date)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "account_id": r.account_id,
            "video_id": r.video_id,
            "title": r.title,
            "publish_date": str(r.publish_date) if r.publish_date else None,
            "plays": r.plays,
            "recommends": r.recommends,
            "likes_thumb": r.likes_thumb,
            "comments": r.comments,
            "shares": r.shares,
            "new_fans": r.new_fans,
            "forwards_chat_moments": r.forwards_chat_moments,
            "set_as_ringtone": r.set_as_ringtone,
            "set_as_status": r.set_as_status,
            "set_as_moments_cover": r.set_as_moments_cover,
            "wecom_link_clicks": r.wecom_link_clicks,
            "wecom_link_click_users": r.wecom_link_click_users,
            "added_to_contacts": r.added_to_contacts,
            "added_to_contacts_users": r.added_to_contacts_users,
            "avg_watch_duration": r.avg_watch_duration,
            "completion_rate": r.completion_rate,
        }
        for r in rows
    ]


@router.get("/overview")
async def channels_overview(
    account_id: int | None = Query(None),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(
        func.count(WxChannelsPost.id).label("posts"),
        func.sum(WxChannelsPost.plays).label("plays"),
        func.sum(WxChannelsPost.recommends).label("recommends"),
        func.sum(WxChannelsPost.likes_thumb).label("likes_thumb"),
        func.sum(WxChannelsPost.comments).label("comments"),
        func.sum(WxChannelsPost.shares).label("shares"),
        func.sum(WxChannelsPost.new_fans).label("new_fans"),
        func.sum(WxChannelsPost.forwards_chat_moments).label("forwards_chat_moments"),
    )
    if account_id is not None:
        stmt = stmt.where(WxChannelsPost.account_id == account_id)
    if start_date:
        stmt = stmt.where(WxChannelsPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(WxChannelsPost.publish_date <= end_date)

    row = (await session.execute(stmt)).one()
    posts = int(row.posts or 0)
    plays = int(row.plays or 0)
    return {
        "posts": posts,
        "plays": plays,
        "recommends": int(row.recommends or 0),
        "likes_thumb": int(row.likes_thumb or 0),
        "comments": int(row.comments or 0),
        "shares": int(row.shares or 0),
        "new_fans": int(row.new_fans or 0),
        "forwards_chat_moments": int(row.forwards_chat_moments or 0),
        "avg_plays_per_post": round(plays / posts, 1) if posts else 0,
    }

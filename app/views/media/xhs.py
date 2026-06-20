"""API endpoints for Xiaohongshu (小红书) accounts, uploads, and post queries."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import tempfile

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_active_user, current_admin_user, current_analyst_user
from ... import db as _db_mod
from ...db import get_session
from ...db.etl.xhs import parse_xhs_xlsx, upsert_xhs_posts
from ...db.models import XhsAccount, XhsPost
from ...utils.logger import log_operation

router = APIRouter(prefix="/media/xhs", tags=["xhs"])

_logger = logging.getLogger(__name__)
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


# ── Account CRUD ──────────────────────────────────────────────────────────────

class XhsAccountCreate(BaseModel):
    name: str


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_xhs_account(
    body: XhsAccountCreate,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy.exc import IntegrityError
    acc = XhsAccount(name=body.name.strip())
    session.add(acc)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Account '{body.name.strip()}' already exists")
    await session.refresh(acc)
    return {"id": acc.id, "name": acc.name, "is_active": acc.is_active}


@router.get("/accounts")
async def list_xhs_accounts(
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(XhsAccount).order_by(XhsAccount.created_at)
    )).scalars().all()
    return [{"id": a.id, "name": a.name, "is_active": a.is_active} for a in rows]


class XhsAccountUpdate(BaseModel):
    name: str


@router.patch("/accounts/{account_id}")
async def rename_xhs_account(
    account_id: int,
    body: XhsAccountUpdate,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy.exc import IntegrityError
    acc = await session.get(XhsAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Account not found")
    acc.name = body.name.strip()
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Account '{body.name.strip()}' already exists")
    await session.refresh(acc)
    return {"id": acc.id, "name": acc.name, "is_active": acc.is_active}


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_xhs_account(
    account_id: int,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    acc = await session.get(XhsAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Account not found")
    await session.delete(acc)
    await session.commit()


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_xhs(
    account_id: int = Form(...),
    file: UploadFile = File(...),
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Upload a Xiaohongshu xlsx export and upsert posts for a specific account.

    Dedup key: (account_id, title, publish_date).  Posts absent from this file
    are left untouched in the DB.
    """
    # Verify account exists
    acc = await session.get(XhsAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"XHS account {account_id} not found")

    filename = file.filename or "upload.xlsx"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 xlsx 或 xls 文件。")

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
            df_raw = pd.read_excel(path, header=None, dtype=str)
            rows = parse_xhs_xlsx(df_raw)
            if not rows:
                raise ValueError("文件中未解析到有效行，请确认格式正确。")
            with _db_mod.SyncSessionLocal() as sync_sess:
                return upsert_xhs_posts(rows, account_id, sync_sess)

        result = await asyncio.to_thread(_process, tmp_path)

    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("xhs_upload_failed filename=%r account=%d: %s",
                      filename, account_id, exc, exc_info=exc)
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    await log_operation(
        str(_user.id),
        "xhs_upload",
        {"filename": filename, "account_id": account_id,
         "total": result["total"], "upserted": result["upserted"]},
        session=session,
    )
    return result


# ── Post listing ──────────────────────────────────────────────────────────────

@router.get("/posts")
async def list_xhs_posts(
    account_id: int | None = Query(None),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(XhsPost).order_by(XhsPost.publish_date.desc()).limit(limit)
    if account_id is not None:
        stmt = stmt.where(XhsPost.account_id == account_id)
    if start_date:
        stmt = stmt.where(XhsPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(XhsPost.publish_date <= end_date)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "account_id": r.account_id,
            "title": r.title,
            "publish_date": str(r.publish_date),
            "genre": r.genre,
            "impressions": r.impressions,
            "views": r.views,
            "cover_click_rate": r.cover_click_rate,
            "likes": r.likes,
            "comments": r.comments,
            "collects": r.collects,
            "new_followers": r.new_followers,
            "shares": r.shares,
            "avg_watch_time": r.avg_watch_time,
            "danmu": r.danmu,
        }
        for r in rows
    ]

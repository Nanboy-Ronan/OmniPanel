"""Pugongying (蒲公英) KOL/KOC collaboration data endpoints."""

import asyncio
import datetime as dt
import logging
import os
import tempfile

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_active_user, current_analyst_user
from ... import db as _db_mod
from ...db import get_session
from ...db.etl.pgy import parse_pgy_xlsx, upsert_pgy_notes
from ...db.models import XhsAccount, PgyNote
from ...utils.logger import log_operation

router = APIRouter(prefix="/media/pgy", tags=["pugongying"])

_logger = logging.getLogger(__name__)
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

@router.post("/upload")
async def upload_pgy(
    account_id: int = Form(...),
    file: UploadFile = File(...),
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Upload a Pugongying xlsx export and upsert posts for a specific account."""
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
            rows = parse_pgy_xlsx(df_raw)
            if not rows:
                raise ValueError("文件中未解析到有效行，请确认格式正确。")
            with _db_mod.SyncSessionLocal() as sync_sess:
                return upsert_pgy_notes(rows, account_id, sync_sess)

        result = await asyncio.to_thread(_process, tmp_path)

    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("pgy_upload_failed filename=%r account=%d: %s",
                      filename, account_id, exc, exc_info=exc)
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    await log_operation(
        str(_user.id),
        "pgy_upload",
        {"filename": filename, "account_id": account_id,
         "total": result["total"], "upserted": result["upserted"]},
        session=session,
    )
    return result

@router.get("/notes")
async def list_pgy_notes(
    account_id: int | None = Query(None),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    limit: int = Query(500, ge=1, le=1000),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(PgyNote).order_by(PgyNote.publish_date.desc()).limit(limit)
    if account_id is not None:
        stmt = stmt.where(PgyNote.account_id == account_id)
    if start_date:
        stmt = stmt.where(PgyNote.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(PgyNote.publish_date <= end_date)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            c.name: str(getattr(r, c.name)) if isinstance(getattr(r, c.name), (dt.date, dt.datetime)) else getattr(r, c.name)
            for c in r.__table__.columns
        }
        for r in rows
    ]

@router.get("/bloggers")
async def aggregate_bloggers(
    account_id: int | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(
        PgyNote.blogger_nickname,
        func.count().label("total_cooperations"),
        func.sum(PgyNote.blogger_quote).label("total_spend"),
        func.avg(PgyNote.cost_per_interaction).label("avg_cpe"),
        func.sum(PgyNote.interactions).label("total_interactions"),
    )
    if account_id is not None:
        stmt = stmt.where(PgyNote.account_id == account_id)
    
    stmt = stmt.group_by(PgyNote.blogger_nickname).order_by(func.count().desc())
    rows = (await session.execute(stmt)).all()
    
    return [
        {
            "blogger_nickname": r.blogger_nickname,
            "total_cooperations": r.total_cooperations,
            "total_spend": float(r.total_spend) if r.total_spend is not None else 0.0,
            "avg_cpe": float(r.avg_cpe) if r.avg_cpe is not None else 0.0,
            "total_interactions": int(r.total_interactions) if r.total_interactions is not None else 0,
            # avg_interaction_rate is just (total_interactions / impressions) if impressions > 0 else 0, wait, PgyNote has interaction_rate string, we might not be able to sum it, but we can compute it if needed. 
        }
        for r in rows
    ]

@router.get("/campaigns")
async def aggregate_campaigns(
    account_id: int | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(
        PgyNote.cooperation_name,
        func.count().label("total_notes"),
        func.sum(PgyNote.blogger_quote + PgyNote.service_fee).label("total_spend"),
        func.sum(PgyNote.impressions).label("total_impressions"),
        func.sum(PgyNote.interactions).label("total_interactions"),
        func.avg(PgyNote.cost_per_interaction).label("avg_cpe"),
    )
    if account_id is not None:
        stmt = stmt.where(PgyNote.account_id == account_id)
        
    stmt = stmt.group_by(PgyNote.cooperation_name).order_by(func.count().desc())
    rows = (await session.execute(stmt)).all()
    
    return [
        {
            "cooperation_name": r.cooperation_name,
            "total_notes": r.total_notes,
            "total_spend": float(r.total_spend) if r.total_spend is not None else 0.0,
            "total_impressions": int(r.total_impressions) if r.total_impressions is not None else 0,
            "total_interactions": int(r.total_interactions) if r.total_interactions is not None else 0,
            "avg_cpe": float(r.avg_cpe) if r.avg_cpe is not None else 0.0,
        }
        for r in rows
    ]

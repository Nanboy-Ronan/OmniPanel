"""API endpoints for Zhihu (知乎) uploads and post queries."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import tempfile
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_active_user, current_analyst_user
from ... import db as _db_mod
from ...db import get_session
from ...db.etl.zhihu import parse_zhihu_csv, upsert_zhihu_posts, VALID_CONTENT_TYPES
from ...db.models import ZhihuPost
from ...utils.logger import log_operation

router = APIRouter(prefix="/media/zhihu", tags=["zhihu"])

_logger = logging.getLogger(__name__)
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _read_zhihu_file(path: str) -> pd.DataFrame:
    """Read a Zhihu export file as a DataFrame.

    Files exported by Zhihu are UTF-8 BOM CSV despite often carrying a .xls
    extension.  Try CSV first; fall back to openpyxl for genuine xlsx files.
    """
    try:
        return pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    except Exception:
        pass
    return pd.read_excel(path, dtype=str)


@router.post("/upload")
async def upload_zhihu(
    content_type: Literal["article", "qa"] = Form(...),
    file: UploadFile = File(...),
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Upload a Zhihu export file (CSV or xls) and upsert posts.

    content_type must be 'article' or 'qa'.
    Dedup key: (content_type, title, publish_date).
    Posts absent from this upload are left untouched in the DB.
    """
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".csv", ".xls", ".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 csv、xls 或 xlsx 文件。")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".csv") as tmp:
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
            df = _read_zhihu_file(path)
            rows = parse_zhihu_csv(df, content_type)
            if not rows:
                raise ValueError("文件中未解析到有效行，请确认格式正确。")
            with _db_mod.SyncSessionLocal() as sync_sess:
                return upsert_zhihu_posts(rows, sync_sess)

        result = await asyncio.to_thread(_process, tmp_path)

    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("zhihu_upload_failed filename=%r content_type=%s: %s",
                      filename, content_type, exc, exc_info=exc)
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    await log_operation(
        str(_user.id),
        "zhihu_upload",
        {"filename": filename, "content_type": content_type,
         "total": result["total"], "upserted": result["upserted"]},
        session=session,
    )
    return result


@router.get("/posts")
async def list_zhihu_posts(
    content_type: str | None = Query(None),
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(ZhihuPost).order_by(ZhihuPost.publish_date.desc()).limit(limit)
    if content_type is not None:
        stmt = stmt.where(ZhihuPost.content_type == content_type)
    if start_date:
        stmt = stmt.where(ZhihuPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(ZhihuPost.publish_date <= end_date)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "content_type": r.content_type,
            "title": r.title,
            "publish_date": str(r.publish_date),
            "url": r.url,
            "reads": r.reads,
            "plays": r.plays,
            "likes": r.likes,
            "favorites": r.favorites,
            "comments": r.comments,
            "collects": r.collects,
            "shares": r.shares,
        }
        for r in rows
    ]

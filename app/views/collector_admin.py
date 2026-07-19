"""Admin endpoints for the creator-portal export agent (小红书/知乎自动采集).

Session files (Playwright storage_state.json) are produced locally by
`python -m app.collector bootstrap-login` and uploaded here so the collector
process on the server can use them. See app/collector/ and docs/collector.md.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_admin_user
from ..db import get_session
from ..db.models import CollectorRun, XhsAccount
from ..collector.paths import session_path, sessions_dir
from ..utils.logger import log_operation

router = APIRouter(prefix="/admin/collector", tags=["collector"])

_MAX_SESSION_BYTES = 1024 * 1024  # storage_state.json is a few KB; 1MB is generous


def _parse_session_filename(name: str) -> tuple[str, int | None] | None:
    """xhs_{id}.json -> ("xhs", id); zhihu.json -> ("zhihu", None); else None."""
    stem = name[:-5] if name.endswith(".json") else None
    if stem is None:
        return None
    if stem == "zhihu":
        return "zhihu", None
    if stem.startswith("xhs_"):
        try:
            return "xhs", int(stem[len("xhs_"):])
        except ValueError:
            return None
    return None


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def upload_collector_session(
    platform: Literal["xhs", "zhihu"] = Form(...),
    account_id: int | None = Form(None),
    file: UploadFile = File(...),
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Upload a storage_state.json produced by `bootstrap-login`."""
    if platform == "xhs":
        if account_id is None:
            raise HTTPException(status_code=422, detail="xhs 平台必须提供 account_id")
        acc = await session.get(XhsAccount, account_id)
        if acc is None:
            raise HTTPException(status_code=404, detail=f"XHS account {account_id} not found")

    raw = await file.read(_MAX_SESSION_BYTES + 1)
    if len(raw) > _MAX_SESSION_BYTES:
        raise HTTPException(status_code=413, detail="登录态文件过大（上限 1 MB）。")

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="不是合法的 JSON 文件。")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("cookies"), list):
        raise HTTPException(status_code=400, detail="不是合法的 storage_state.json（缺少 cookies 字段）。")

    target_path = session_path(platform, account_id if platform == "xhs" else None)
    tmp_path = target_path.with_suffix(".json.tmp")
    tmp_path.write_bytes(raw)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, target_path)

    await log_operation(
        str(_user.id),
        "collector_session_upload",
        {"platform": platform, "account_id": account_id},
        session=session,
    )
    return {"platform": platform, "account_id": account_id, "status": "saved"}


@router.get("/sessions")
async def list_collector_sessions(
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """List saved session files plus each target's most recent run status."""
    accounts = {
        a.id: a.name
        for a in (await session.execute(select(XhsAccount))).scalars().all()
    }

    results = []
    for f in sorted(sessions_dir().glob("*.json")):
        parsed = _parse_session_filename(f.name)
        if parsed is None:
            continue
        platform, account_id = parsed
        stat = f.stat()

        run_stmt = (
            select(CollectorRun)
            .where(CollectorRun.platform == platform)
            .order_by(CollectorRun.started_at.desc())
            .limit(1)
        )
        if platform == "xhs":
            run_stmt = run_stmt.where(CollectorRun.account_id == account_id)
        last_run = (await session.execute(run_stmt)).scalars().first()

        results.append({
            "platform": platform,
            "account_id": account_id,
            "account_name": accounts.get(account_id) if account_id is not None else None,
            "updated_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "size_bytes": stat.st_size,
            "last_run_status": last_run.status if last_run else None,
            "last_run_at": last_run.started_at.isoformat() if last_run else None,
        })
    return results


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collector_session(
    platform: Literal["xhs", "zhihu"] = Query(...),
    account_id: int | None = Query(None),
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    path = session_path(platform, account_id if platform == "xhs" else None)
    if not path.exists():
        raise HTTPException(status_code=404, detail="登录态文件不存在")
    path.unlink()
    await log_operation(
        str(_user.id),
        "collector_session_delete",
        {"platform": platform, "account_id": account_id},
        session=session,
    )


@router.get("/runs")
async def list_collector_runs(
    limit: int = Query(50, ge=1, le=500),
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(CollectorRun).order_by(CollectorRun.started_at.desc()).limit(limit)
    )).scalars().all()
    return [
        {
            "id": r.id,
            "platform": r.platform,
            "account_id": r.account_id,
            "content_type": r.content_type,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "rows_upserted": r.rows_upserted,
            "filename": r.filename,
            "error_message": r.error_message,
            "triggered_by": r.triggered_by,
        }
        for r in rows
    ]

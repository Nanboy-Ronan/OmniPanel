"""Weekly media report — internal JSON API.

These routes exist to be called by the Streamlit app (app/ui/pages/weekly_report.py),
not to be opened directly. The FastAPI backend (port 8000) is not exposed to
the public internet on a typical deployment — only the Streamlit frontend is,
usually via a reverse proxy. A link straight to one of these routes would
401 for anyone who isn't already holding a bearer token, which a browser tap
from WeCom never has. The WeCom push (app/reports/service.py) therefore links
to the public Streamlit URL (settings.public_base_url), not to anything here.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user, current_admin_user
from ..db import get_session
from ..db.models import WeeklyReportRun
from ..reports.service import generate_weekly_report

router = APIRouter(prefix="/reports", tags=["reports"])
admin_router = APIRouter(prefix="/admin/reports", tags=["reports"])


class WeeklyReportSummary(BaseModel):
    id: int
    week_start: date
    week_end: date
    status: str
    wecom_sent: bool

    class Config:
        from_attributes = True


class WeeklyReportDetail(WeeklyReportSummary):
    narrative: str | None
    html_content: str | None
    error_message: str | None


@router.get("/weekly", response_model=list[WeeklyReportSummary])
async def list_weekly_reports(
    limit: int = Query(20, ge=1, le=100),
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(WeeklyReportRun).order_by(WeeklyReportRun.week_start.desc()).limit(limit)
    )
    return result.scalars().all()


@router.get("/weekly/{run_id}", response_model=WeeklyReportDetail)
async def get_weekly_report(
    run_id: int,
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    run = await session.get(WeeklyReportRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")
    return run


@admin_router.post("/weekly/run", response_model=WeeklyReportDetail)
async def trigger_weekly_report(
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Manually generate (or retry) the weekly report — for verifying the
    full pipeline (including the real WeCom push) without waiting for the
    scheduled loop to fire."""
    return await generate_weekly_report(session)

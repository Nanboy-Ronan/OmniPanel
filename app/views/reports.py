"""Authenticated weekly-report JSON API.

React calls these routes through the same-origin /api/ proxy. Notification links
point to the public workbench so the reader can authenticate before opening a
report; the API itself still requires a bearer token.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user, current_admin_user
from ..config import settings
from ..db import get_session
from ..db.models import WeeklyReportRun, WorkBuddyReportJob
from ..reports.service import generate_weekly_report
from ..utils.logger import log_operation

router = APIRouter(prefix="/reports", tags=["reports"])
admin_router = APIRouter(prefix="/admin/reports", tags=["reports"])


class WeeklyReportSummary(BaseModel):
    id: int
    week_start: date
    week_end: date
    generated_at: datetime
    status: str
    wecom_sent: bool
    job_state: str | None = None
    narrative_status: str | None = None

    class Config:
        from_attributes = True


class WeeklyReportDetail(WeeklyReportSummary):
    narrative: str | None
    html_content: str | None
    error_message: str | None
    delivery_error: str | None = None


def report_json(run, job=None, *, detail=False):
    schema = WeeklyReportDetail if detail else WeeklyReportSummary
    result = schema.model_validate(run).model_dump()
    if job:
        result.update(job_state=job.state, narrative_status=job.narrative_status)
        if detail: result["delivery_error"] = job.delivery_error
    return result


@router.get("/weekly", response_model=list[WeeklyReportSummary])
async def list_weekly_reports(
    limit: int = Query(20, ge=1, le=100),
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(WeeklyReportRun, WorkBuddyReportJob).outerjoin(WorkBuddyReportJob, WorkBuddyReportJob.report_id == WeeklyReportRun.id)
        .order_by(WeeklyReportRun.week_start.desc()).limit(limit)
    )
    return [report_json(run, job) for run, job in result.all()]


@router.get("/weekly/{run_id}", response_model=WeeklyReportDetail)
async def get_weekly_report(
    run_id: int,
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    run = await session.get(WeeklyReportRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")
    return report_json(run, await session.get(WorkBuddyReportJob, run_id), detail=True)


@admin_router.post("/weekly/run", response_model=WeeklyReportDetail)
async def trigger_weekly_report(
    request: Request,
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Manually generate (or retry) the weekly report — for verifying the
    full pipeline (including the real WeCom push) without waiting for the
    scheduled loop to fire."""
    try:
        run = await generate_weekly_report(session)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    await log_operation(str(_user.id), "weekly_report_run", {"report_id": run.id, "week_start": run.week_start, "status": run.status, "wecom_sent": run.wecom_sent}, request=request)
    return report_json(run, await session.get(WorkBuddyReportJob, run.id), detail=True)


class DeliveryAction(BaseModel):
    action: Literal["retry", "mark_sent"]
    acknowledged_uncertain_delivery: bool = False


@admin_router.post("/weekly/{run_id}/delivery", response_model=WeeklyReportDetail)
async def resolve_delivery(run_id: int, body: DeliveryAction, request: Request,
                           user=Depends(current_admin_user), session: AsyncSession = Depends(get_session)):
    job = (await session.execute(select(WorkBuddyReportJob).where(WorkBuddyReportJob.report_id == run_id).with_for_update())).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "未找到 WorkBuddy 周报任务")
    run = await session.get(WeeklyReportRun, run_id)
    if job.state not in {"delivery_failed", "delivery_unknown"}:
        raise HTTPException(409, "当前状态不允许重试或确认投递")
    if job.state == "delivery_unknown" and not body.acknowledged_uncertain_delivery:
        raise HTTPException(409, "请先核对是否已收到；重试可能重复推送。")
    if body.action == "mark_sent":
        if job.state != "delivery_unknown":
            raise HTTPException(409, "只有投递结果未确认的任务可以标记已收到")
        job.state, run.wecom_sent = "sent", True
    else:
        if settings.report_provider != "workbuddy":
            raise HTTPException(409, "请先启用 WorkBuddy 周报 worker")
        from ..reports.workbuddy_delivery import validate_recipients
        try:
            await validate_recipients(session, job.recipients)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        job.state = "ready"
    job.delivery_error = None
    await log_operation(str(user.id), "workbuddy_delivery_action", {"report_id": run_id, "action": body.action}, session=session, request=request)
    return report_json(run, job, detail=True)

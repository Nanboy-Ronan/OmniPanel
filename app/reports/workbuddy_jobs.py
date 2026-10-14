"""Postgres-backed report queue. Agent execution happens outside DB transactions."""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from .. import db
from ..config import settings
from ..db.models import WeeklyReportRun, WorkBuddyReportJob
from ..integrations.workbuddy import reports as provider
from .render import render_html, attach_narrative
from .service import _summary_lines
from .weekly_media import build_report_context, week_bounds
from .workbuddy_snapshot import build_snapshot, PROMPT_VERSION
from .workbuddy_delivery import configured_recipients, validate_recipients, send_report
from ..utils.wecom_bot import truncate_utf8

logger = logging.getLogger(__name__)
FALLBACK = "智能解读暂缺，以下保留平台已计算的数据。"
BUSY = ("preparing", "generating", "sending")


def now():
    return datetime.now(timezone.utc)


def notification(summary, report_id, has_narrative):
    link = f"{settings.public_origin}/console/?page=reports&report={report_id}"
    tail = f"\n{'智能解读已生成。' if has_narrative else FALLBACK}\n完整周报：{link}"
    budget = max(0, 2048 - len(tail.encode('utf-8')))
    return (truncate_utf8(summary, budget) if budget >= 64 else "") + tail


async def enqueue(session, reference_date=None):
    if not provider.configured():
        raise ValueError("请先配置 WorkBuddy API Key、Agent ID 和来源应用标识。")
    recipients = await configured_recipients(session)
    await validate_recipients(session, recipients)
    reference_date = reference_date or datetime.now(ZoneInfo(settings.app_timezone)).date()
    bounds = week_bounds(reference_date)
    # Share the existing week's uniqueness and mutual exclusion with legacy generation.
    await session.execute(text("SELECT pg_advisory_xact_lock(579343, :week)"), {"week": bounds.this_week_start.toordinal()})
    run = (await session.execute(select(WeeklyReportRun).where(WeeklyReportRun.week_start == bounds.this_week_start))).scalar_one_or_none()
    if run is None:
        run = WeeklyReportRun(week_start=bounds.this_week_start, week_end=bounds.this_week_end, status="queued", wecom_sent=False)
        session.add(run)
        await session.flush()
    job = await session.get(WorkBuddyReportJob, run.id)
    if job is None and run.status == "success":
        # Never overwrite or accidentally redeliver a previously published legacy report.
        await session.commit()
        return run
    if job is None:
        session.add(WorkBuddyReportJob(report_id=run.id, state="queued", recipients=recipients,
                                      agent_id=settings.workbuddy_agent_id, manifest_version="1.0", prompt_version=PROMPT_VERSION))
        run.status = "queued"
        run.error_message = None
    elif job.state == "failed":
        job.state = "queued"
        run.status = "queued"
        run.error_message = None
    # Delivery retry is explicit; unknown delivery must never be silently repeated.
    await session.commit()
    return run


async def _owned(session, report_id, token):
    job = (await session.execute(select(WorkBuddyReportJob).where(
        WorkBuddyReportJob.report_id == report_id, WorkBuddyReportJob.lease_token == token,
    ).with_for_update())).scalar_one_or_none()
    if job is None:
        raise provider.WorkBuddyError("lease_lost")
    return job


async def _claim():
    async with db.AsyncSessionLocal() as session:
        job = (await session.execute(select(WorkBuddyReportJob).where(
            WorkBuddyReportJob.state.in_(["queued", "ready"]),
        ).order_by(WorkBuddyReportJob.report_id).with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if job is None:
            return None
        token = uuid.uuid4()
        job.lease_token = token
        job.lease_until = now() + timedelta(seconds=settings.workbuddy_report_timeout_seconds + 240)
        job.state = "preparing" if job.state == "queued" else "sending"
        if job.state == "sending": job.delivery_attempts += 1
        claim = (job.report_id, token, job.state)
        await session.commit()
        return claim


async def _finish_generation(report_id, token, output=None, error=None):
    async with db.AsyncSessionLocal() as session:
        job = await _owned(session, report_id, token)
        run = await session.get(WeeklyReportRun, report_id)
        if not run.html_content or not job.snapshot:
            run.status, job.state = "error", "failed"
            run.error_message = "周报数据准备失败，请检查数据源后重试。"
        else:
            narrative = output.narrative() if output else FALLBACK
            run.narrative = narrative
            run.html_content = attach_narrative(run.html_content, narrative)
            # Keep push compact and its authenticated link intact. Full narrative is in the report.
            run.notification_text = notification(run.notification_text, report_id, bool(output))
            run.status, job.state = "success", "ready"
            run.generated_at = datetime.now(ZoneInfo(settings.app_timezone)).replace(tzinfo=None)
            job.agent_output = output.model_dump() if output else None
        job.narrative_status = "success" if output else "unavailable"
        job.generation_error = error
        job.lease_token = job.lease_until = None
        await session.commit()


async def _generate(report_id, token):
    try:
        async with db.AsyncSessionLocal() as session:
            # Read inputs and render before any Agent invocation. No DB lock while waiting for AI.
            run = await session.get(WeeklyReportRun, report_id)
            reference_date = run.week_end + timedelta(days=1)
            async with asyncio.timeout(150):
                context = await build_report_context(session, reference_date)
            snapshot = build_snapshot(context, report_id)
            html = render_html(context, None)
            summary = "\n".join(_summary_lines(context))
            await session.rollback()
        async with db.AsyncSessionLocal() as session:
            job = await _owned(session, report_id, token)
            run = await session.get(WeeklyReportRun, report_id)
            run.html_content, run.notification_text, run.status = html, summary, "processing"
            job.snapshot, job.state = snapshot, "generating"
            job.prompt_version = snapshot["prompt_version"]
            agent_id, version = job.agent_id, job.manifest_version
            await session.commit()

        async def on_runtime(runtime_id):
            async with db.AsyncSessionLocal() as session:
                job = await _owned(session, report_id, token)
                job.runtime_id, job.cleanup_pending = runtime_id, True
                await session.commit()

        async def on_cleanup(deleted):
            async with db.AsyncSessionLocal() as session:
                job = await _owned(session, report_id, token)
                job.cleanup_pending = not deleted
                await session.commit()

        try:
            output = await provider.generate(snapshot, agent_id, version, on_runtime, on_cleanup)
        except provider.WorkBuddyError as exc:
            await _finish_generation(report_id, token, error=str(exc))
        else:
            await _finish_generation(report_id, token, output=output)
    except asyncio.CancelledError:
        raise  # Lease recovery handles interrupted work after restart.
    except Exception as exc:
        logger.warning("WorkBuddy report failed report=%s type=%s", report_id, type(exc).__name__)
        # Do not overwrite a newer owner's work; allow lease recovery on DB failures.
        if isinstance(exc, provider.WorkBuddyError) and str(exc) == "lease_lost":
            return
        await _finish_generation(report_id, token, error="preparation_failed")


async def _deliver(report_id, token):
    async with db.AsyncSessionLocal() as session:
        job = await _owned(session, report_id, token)
        run = await session.get(WeeklyReportRun, report_id)
        if run.wecom_sent:
            job.state, job.lease_token, job.lease_until = "sent", None, None
            await session.commit()
            return
        try:
            await validate_recipients(session, job.recipients)
        except ValueError:
            job.state, job.delivery_error = "delivery_failed", "收件人权限已失效，请检查后重试。"
            job.lease_token = job.lease_until = None
            await session.commit()
            return
        recipients, message = list(job.recipients), run.notification_text
        await session.rollback()
    # The network call occurs after the sending intent was committed and DB connection released.
    outcome = await asyncio.to_thread(send_report, message, recipients)
    async with db.AsyncSessionLocal() as session:
        job = await _owned(session, report_id, token)
        run = await session.get(WeeklyReportRun, report_id)
        run.wecom_sent = outcome == "sent"
        job.state = {"sent": "sent", "failed": "delivery_failed", "unknown": "delivery_unknown"}[outcome]
        job.delivery_error = None if outcome == "sent" else (
            "发送结果未确认，请核对企业微信是否已收到再决定重试。" if outcome == "unknown" else "发送未成功，请检查企微配置或收件人后重试。")
        job.lease_token = job.lease_until = None
        await session.commit()


async def recover_expired():
    """Never replay a possibly completed Agent turn or a possibly accepted send."""
    async with db.AsyncSessionLocal() as session:
        jobs = (await session.execute(select(WorkBuddyReportJob).where(
            WorkBuddyReportJob.state.in_(BUSY), WorkBuddyReportJob.lease_until < now(),
        ).with_for_update(skip_locked=True))).scalars().all()
        for job in jobs:
            run = await session.get(WeeklyReportRun, job.report_id)
            if job.state == "preparing":
                job.state = "queued"
            elif job.state == "generating":
                run.narrative = FALLBACK
                run.html_content = attach_narrative(run.html_content, FALLBACK)
                run.notification_text = notification(run.notification_text, run.id, False)
                run.status, job.state = "success", "ready"
                run.generated_at = datetime.now(ZoneInfo(settings.app_timezone)).replace(tzinfo=None)
                job.narrative_status, job.generation_error = "unavailable", "interrupted_check_runtime"
            else:
                job.state, job.delivery_error = "delivery_unknown", "投递过程中服务中断，请核对企业微信后再重试。"
            job.lease_token = job.lease_until = None
        await session.commit()


async def process_one():
    claim = await _claim()
    if claim is None:
        return False
    report_id, token, state = claim
    if state == "preparing":
        await _generate(report_id, token)
    else:
        await _deliver(report_id, token)
    return True


async def report_worker_loop():
    while True:
        await recover_expired()
        if not await process_one():
            # Best-effort cleanup recovery after a worker crash. Only IDs created for these jobs.
            async with db.AsyncSessionLocal() as session:
                job = (await session.execute(select(WorkBuddyReportJob).where(
                    WorkBuddyReportJob.cleanup_pending.is_(True), WorkBuddyReportJob.runtime_id.is_not(None),
                    WorkBuddyReportJob.state.not_in(BUSY),
                ).limit(1))).scalar_one_or_none()
                cleanup = (job.report_id, job.runtime_id) if job else None
            if cleanup and await provider.cleanup_runtime(cleanup[1]):
                async with db.AsyncSessionLocal() as session:
                    job = await session.get(WorkBuddyReportJob, cleanup[0])
                    if job.runtime_id == cleanup[1]: job.cleanup_pending = False
                    await session.commit()
            await asyncio.sleep(15)

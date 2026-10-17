"""Postgres-backed report queue. Agent execution happens outside DB transactions."""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from .. import db
from ..config import settings
from ..db.models import WeeklyReportRun, WorkBuddyReportJob
from ..integrations.workbuddy import reports as provider
from .render import render_html, attach_narrative
from .service import _summary_lines
from .weekly_media import build_report_context, week_bounds
from .workbuddy_snapshot import build_snapshot, PROMPT_VERSION
from .workbuddy_delivery import configured_recipients, validate_recipients, send_report
from ..services.data_freshness import get_data_freshness
from ..utils.logger import log_operation
from ..utils.wecom_bot import send_wecom_alert, truncate_utf8

logger = logging.getLogger(__name__)
FALLBACK = "智能解读暂缺，以下保留平台已计算的数据。"
BUSY = ("preparing", "generating", "sending")
# A job whose preparation is interrupted this many times stops re-queueing itself.
MAX_PREPARE_ATTEMPTS = 3
# Runtime cleanup back-off per report, process-local: a failing delete must not starve others.
_cleanup_backoff: dict[int, tuple[float, float]] = {}


def now():
    return datetime.now(timezone.utc)


def notification(summary, report_id, has_narrative):
    link = f"{settings.public_origin}/console/?page=reports&report={report_id}"
    tail = f"\n{'智能解读已生成。' if has_narrative else FALLBACK}\n完整周报：{link}"
    budget = max(0, 2048 - len(tail.encode('utf-8')))
    return (truncate_utf8(summary, budget) if budget >= 64 else "") + tail


def _week(run):
    return f"{run.week_start} ~ {run.week_end}"


async def _alert(text):
    """Failure notices go to the existing alert recipients; never mask the job state."""
    try:
        await asyncio.to_thread(send_wecom_alert, "[周报告警] " + text)
    except Exception:
        logger.exception("WorkBuddy report alert failed")


async def _audit(action, detail):
    # System events (scheduler/worker) have no acting user.
    try:
        await log_operation(None, action, detail)
    except Exception:
        logger.exception("WorkBuddy report audit failed action=%s", action)


async def enqueue(session, reference_date=None, *, trigger="schedule"):
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
    event = None
    if job is None and run.status == "success":
        if run.wecom_sent or not run.notification_text:
            # Never overwrite or accidentally redeliver a previously published legacy report.
            await session.commit()
            return run
        # A legacy report generated but never delivered before the provider switch:
        # keep its archived content and deliver it through the WorkBuddy fence.
        session.add(WorkBuddyReportJob(report_id=run.id, state="ready", recipients=recipients,
                                      agent_id=settings.workbuddy_agent_id, manifest_version="1.0",
                                      prompt_version="legacy", narrative_status="legacy"))
        event = "legacy_handoff"
    elif job is None:
        session.add(WorkBuddyReportJob(report_id=run.id, state="queued", recipients=recipients,
                                      agent_id=settings.workbuddy_agent_id, manifest_version="1.0", prompt_version=PROMPT_VERSION))
        run.status = "queued"
        run.error_message = None
        event = "queued"
    elif job.state == "failed":
        job.state = "queued"
        job.prepare_attempts = 0
        run.status = "queued"
        run.error_message = None
        event = "requeued"
    # Delivery retry is explicit; unknown delivery must never be silently repeated.
    await session.commit()
    if event:
        await _audit("workbuddy_report_queued", {"report_id": run.id, "week_start": str(run.week_start),
                                                 "event": event, "trigger": trigger})
    return run


async def regenerate(session, report_id):
    """Explicit retry for a failed job of any week, not only the current one."""
    job = (await session.execute(select(WorkBuddyReportJob).where(
        WorkBuddyReportJob.report_id == report_id).with_for_update())).scalar_one_or_none()
    if job is None or job.state != "failed":
        raise ValueError("只有生成失败的周报可以重新生成")
    run = await session.get(WeeklyReportRun, report_id)
    job.state, job.prepare_attempts, job.generation_error = "queued", 0, None
    run.status, run.error_message = "queued", None
    return run, job


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
        else: job.prepare_attempts += 1
        claim = (job.report_id, token, job.state)
        await session.commit()
        return claim


async def _finish_generation(report_id, token, output=None, error=None):
    async with db.AsyncSessionLocal() as session:
        job = await _owned(session, report_id, token)
        run = await session.get(WeeklyReportRun, report_id)
        failed = not run.html_content or not job.snapshot
        if failed:
            run.status, job.state = "error", "failed"
            run.error_message = "周报数据准备失败，请检查数据源后重试。"
        else:
            narrative = output.narrative() if output else FALLBACK
            run.narrative = narrative
            run.html_content = attach_narrative(run.html_content, narrative)
            # Keep push compact and its authenticated link intact. Full narrative is in the report.
            run.notification_text = notification(run.notification_text, report_id, bool(output))
            run.status, job.state = "success", "ready"
            # Same clock as the legacy path and the column default (database time, UTC).
            run.generated_at = func.now()
            job.agent_output = output.model_dump() if output else None
        job.narrative_status = "success" if output else "unavailable"
        job.generation_error = error
        job.lease_token = job.lease_until = None
        week = _week(run)
        await session.commit()
    await _audit("workbuddy_report_generated", {"report_id": report_id, "state": "failed" if failed else "ready",
                                                "narrative_status": "success" if output else "unavailable", "error": error})
    if failed:
        await _alert(f"WorkBuddy 周报生成失败（{week}），请在周报页查看原因并重新生成。")
    elif not output:
        await _alert(f"WorkBuddy 智能解读未生成（{week}，{error or 'unavailable'}），周报将以数据版继续推送。")


async def _generate(report_id, token):
    try:
        async with db.AsyncSessionLocal() as session:
            # Read inputs and render before any Agent invocation. No DB lock while waiting for AI.
            run = await session.get(WeeklyReportRun, report_id)
            reference_date = run.week_end + timedelta(days=1)
            async with asyncio.timeout(150):
                context = await build_report_context(session, reference_date)
                freshness = await get_data_freshness(session)
            snapshot = build_snapshot(context, report_id, freshness)
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
        week = _week(run)
        try:
            await validate_recipients(session, job.recipients)
        except ValueError:
            job.state, job.delivery_error = "delivery_failed", "收件人权限已失效，请检查后重试。"
            job.lease_token = job.lease_until = None
            await session.commit()
            await _audit("workbuddy_report_delivery", {"report_id": report_id, "outcome": "recipients_invalid"})
            await _alert(f"WorkBuddy 周报未推送（{week}）：收件人权限已失效，请在周报页检查后重试。")
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
    await _audit("workbuddy_report_delivery", {"report_id": report_id, "outcome": outcome, "recipients": len(recipients)})
    if outcome == "failed":
        await _alert(f"WorkBuddy 周报推送失败（{week}），请检查企微配置后在周报页重试。")
    elif outcome == "unknown":
        await _alert(f"WorkBuddy 周报推送结果未确认（{week}），请先核对是否已收到，再在周报页处理。")


async def recover_expired():
    """Never replay a possibly completed Agent turn or a possibly accepted send."""
    alerts = []
    async with db.AsyncSessionLocal() as session:
        jobs = (await session.execute(select(WorkBuddyReportJob).where(
            WorkBuddyReportJob.state.in_(BUSY), WorkBuddyReportJob.lease_until < now(),
        ).with_for_update(skip_locked=True))).scalars().all()
        for job in jobs:
            run = await session.get(WeeklyReportRun, job.report_id)
            if job.state == "preparing":
                if job.prepare_attempts >= MAX_PREPARE_ATTEMPTS:
                    # Repeated crashes while preparing (e.g. OOM) must not loop forever.
                    job.state, job.generation_error = "failed", "preparation_interrupted"
                    run.status, run.error_message = "error", "周报数据准备多次中断，请检查服务状态后重新生成。"
                    alerts.append(f"WorkBuddy 周报数据准备多次中断（{_week(run)}），已停止自动重试，请检查服务后重新生成。")
                else:
                    job.state = "queued"
            elif job.state == "generating":
                run.narrative = FALLBACK
                try:
                    run.html_content = attach_narrative(run.html_content or "", FALLBACK)
                except ValueError:
                    # A malformed archive must not block recovery of every other job.
                    logger.warning("WorkBuddy report %s has no narrative slot; keeping archived HTML", run.id)
                run.notification_text = notification(run.notification_text or "", run.id, False)
                run.status, job.state = "success", "ready"
                run.generated_at = func.now()
                job.narrative_status, job.generation_error = "unavailable", "interrupted_check_runtime"
            else:
                job.state, job.delivery_error = "delivery_unknown", "投递过程中服务中断，请核对企业微信后再重试。"
                alerts.append(f"WorkBuddy 周报推送过程中服务中断（{_week(run)}），结果未确认，请核对是否已收到后在周报页处理。")
            job.lease_token = job.lease_until = None
        await session.commit()
    for text_ in alerts:
        await _alert(text_)


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


async def cleanup_one(clock=time.monotonic):
    """Best-effort cleanup recovery after a worker crash. Only IDs created for these jobs."""
    async with db.AsyncSessionLocal() as session:
        rows = (await session.execute(select(WorkBuddyReportJob.report_id, WorkBuddyReportJob.runtime_id).where(
            WorkBuddyReportJob.cleanup_pending.is_(True), WorkBuddyReportJob.runtime_id.is_not(None),
            WorkBuddyReportJob.state.not_in(BUSY),
        ).order_by(WorkBuddyReportJob.report_id))).all()
    current = clock()
    cleanup = next((row for row in rows if _cleanup_backoff.get(row.report_id, (0, 0))[0] <= current), None)
    if cleanup is None:
        return False
    if await provider.cleanup_runtime(cleanup.runtime_id):
        _cleanup_backoff.pop(cleanup.report_id, None)
        async with db.AsyncSessionLocal() as session:
            job = await session.get(WorkBuddyReportJob, cleanup.report_id)
            if job.runtime_id == cleanup.runtime_id: job.cleanup_pending = False
            await session.commit()
    else:
        delay = min(3600, _cleanup_backoff.get(cleanup.report_id, (0, 30))[1] * 2)
        _cleanup_backoff[cleanup.report_id] = (current + delay, delay)
    return True


async def report_worker_loop():
    while True:
        await recover_expired()
        if not await process_one():
            await cleanup_one()
            await asyncio.sleep(15)

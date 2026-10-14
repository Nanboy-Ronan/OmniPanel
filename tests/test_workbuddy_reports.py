"""Cloud adapter, durable jobs and delivery tests; no real cloud calls or messages."""
import asyncio
import json
import uuid
from datetime import date, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text

from app import db
from app.config import settings
from app.db.models import User, WeeklyReportRun, WorkBuddyReportJob
from app.integrations.workbuddy import reports as provider
from app.reports import workbuddy_jobs as jobs, workbuddy_delivery as delivery
from app.reports.render import attach_narrative
from app.reports.workbuddy_snapshot import AgentReport, build_snapshot, validate_output
from app.reports.weekly_media import week_bounds
from test_weekly_report import async_session_factory

OUTPUT = {"summary": "本周数据可供参考。", "highlights": [], "risks": [], "actions": [], "data_notes": []}
SNAPSHOT = {"snapshot_id": "weekly-test", "metrics": [{"metric_id": "m", "value": 12}]}


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr(settings, "report_provider", "workbuddy")
    monkeypatch.setattr(settings, "workbuddy_api_key", SecretStr("synthetic-key"))
    monkeypatch.setattr(settings, "workbuddy_agent_id", "agent-test")
    monkeypatch.delenv("WECOM_ALERT_TOUSER", raising=False)


def test_snapshot_excludes_credentials_and_incomplete_sales():
    account = NS(id=8, app_secret="do-not-share", name="private-account-name")
    context = {
        "bounds": week_bounds(date(2026, 9, 15)),
        "wechat_sections": [{"account": account, "totals_this_week": {"read_user_count": 3},
                             "totals_last_week": {"read_user_count": 2},
                             "articles": [{"complete": False, "title": "untrusted article"}],
                             "follower": {"available": False, "error": "secret error"}}],
        "ecommerce_section": {"complete": False, "this_week_total": {"gmv": 100}},
    }
    snapshot = build_snapshot(context, 1)
    serialized = json.dumps(snapshot)
    for private in ("do-not-share", "private-account-name", "untrusted article", "secret error", "gmv"):
        assert private not in serialized
    assert snapshot["metrics"][0]["complete"] is False
    assert any("商城" in warning for warning in snapshot["warnings"])


def test_output_references_and_html_are_validated():
    output = {**OUTPUT, "highlights": [{"text": "销售增加", "metric_ids": ["invented"]}]}
    with pytest.raises(ValueError):
        validate_output(json.dumps(output), SNAPSHOT)
    with pytest.raises(ValueError):
        validate_output("```json\n" + json.dumps(OUTPUT) + "```", SNAPSHOT)
    html = attach_narrative("<html><!--WORKBUDDY_NARRATIVE--></html>", "<script>alert(1)</script>")
    assert "<script>" not in html and "&lt;script&gt;" in html


def fake_sdk(raw=None, reason="end_turn", error=None, delete_error=False):
    session = NS(id="123", connect=AsyncMock(), disconnect=AsyncMock())

    async def prompt(prompt_text, options):
        assert "统计快照" in prompt_text
        if error:
            raise error
        def emit(content, session_id="123", kind="agent_message_chunk"):
            options.on_chunk(NS(session_id=session_id, update=NS(session_update=kind, content=NS(type="text", text=content))))
        emit("unrelated session", session_id="456")
        emit("private thinking", kind="agent_thought_chunk")
        value = raw if raw is not None else json.dumps(OUTPUT)
        emit(value[:10])
        emit(value[10:])
        return NS(stop_reason=reason)

    session.prompt = prompt
    runtime = NS(id="123", runtime_info=NS(status="RUNNING"), sessions=NS(default=lambda: session),
                 delete=AsyncMock(side_effect=RuntimeError("private error") if delete_error else None))
    client = NS(runtimes=NS(create=AsyncMock(return_value=runtime)), aclose=AsyncMock())
    return client, runtime, session


@pytest.mark.parametrize("raw,reason,error,code", [
    (None, "end_turn", None, None),
    ("not json", "end_turn", None, "invalid_output"),
    (None, "max_tokens", None, "incomplete_turn"),
    ("a" * 32001, "end_turn", None, "output_too_large"),
    (None, "end_turn", TimeoutError("sensitive-url-token"), "provider_failed"),
])
def test_streaming_completion_and_cleanup(config, caplog, raw, reason, error, code):
    client, runtime, session = fake_sdk(raw, reason, error)
    recorded, cleaned = AsyncMock(), AsyncMock()
    async def run():
        if code:
            with pytest.raises(provider.WorkBuddyError, match=code):
                await provider.generate(SNAPSHOT, "agent-test", "1.0", recorded, cleaned, client_factory=lambda: client)
        else:
            result = await provider.generate(SNAPSHOT, "agent-test", "1.0", recorded, cleaned, client_factory=lambda: client)
            assert result.summary == OUTPUT["summary"]
    asyncio.run(run())
    recorded.assert_awaited_once_with("123")
    cleaned.assert_awaited_once_with(True)
    runtime.delete.assert_awaited_once()
    session.disconnect.assert_awaited_once()
    client.aclose.assert_awaited_once()
    assert "sensitive-url-token" not in caplog.text


def test_cancelled_turn_cleans_up_and_propagates(config):
    client, runtime, _ = fake_sdk(error=asyncio.CancelledError())
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await provider.generate(SNAPSHOT, "agent-test", "1.0", AsyncMock(), AsyncMock(), client_factory=lambda: client)
    asyncio.run(run())
    runtime.delete.assert_awaited_once()


def test_cleanup_failure_is_saved_without_losing_good_output(config):
    client, _, _ = fake_sdk(delete_error=True)
    cleaned = AsyncMock()
    result = asyncio.run(provider.generate(SNAPSHOT, "agent-test", "1.0", AsyncMock(), cleaned, client_factory=lambda: client))
    assert result.summary == OUTPUT["summary"]
    cleaned.assert_awaited_once_with(False)


def test_real_sdk_uses_documented_key_header_and_injects_runtime_secret():
    from cloud_agent_sdk import CloudAgentClient, ManifestBuilder, RuntimeCreateOptions
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": {
            "id": 1234567890, "runtimeName": "synthetic", "status": "RUNNING",
            "createdAt": "2026-10-12T00:00:00Z", "updatedAt": "2026-10-12T00:00:00Z",
        }})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = CloudAgentClient(api_key="synthetic-key", source_app="rpa-test", retry=False, http_client=http)
            manifest = ManifestBuilder().id("agent-test").name("test").version("1.0").build()
            runtime = await client.runtimes.create(RuntimeCreateOptions(runtime_name="synthetic", agent_manifest=manifest))
            assert runtime.sessions.default().id == "1234567890"
            await client.aclose()
    asyncio.run(run())
    assert len(requests) == 1
    request = requests[0]
    assert request.url == "https://www.codebuddy.cn/v2/agentos/runtimes"
    assert request.headers["x-api-key"] == "synthetic-key"
    assert request.headers["x-source-app"] == "rpa-test"
    assert "authorization" not in request.headers
    assert json.loads(request.content)["agentManifest"]["secrets"] == [{"key": "CODEBUDDY_API_KEY", "value": "synthetic-key"}]


@pytest.mark.parametrize("outcome,body,status_code", [
    ("sent", {"errcode": 0}, 200),
    ("unknown", {"errcode": 0, "invaliduser": "bob"}, 200),
    ("failed", {"errcode": 40003}, 200),
    ("unknown", {}, 502),
    ("unknown", {}, 200),
])
def test_delivery_acknowledgement_and_explicit_recipient(monkeypatch, outcome, body, status_code):
    monkeypatch.setattr(delivery, "_env", lambda key: "123" if key == "WECOM_AGENT_ID" else "synthetic")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, request=httpx.Request("GET", "https://test"), json={"access_token": "token"}))
    payloads = []
    def send(*a, **kw):
        payloads.append(kw["json"])
        return httpx.Response(status_code, json=body)
    monkeypatch.setattr(httpx, "post", send)
    assert delivery.send_report("报告" * 2000, ["alice"]) == outcome
    assert payloads[0]["touser"] == "alice"
    assert len(payloads[0]["text"]["content"].encode()) <= 2048
    assert delivery.send_report("报告", ["@all"]) == "failed"
    assert len(payloads) == 1


@pytest.fixture
def queue(async_session_factory, config, monkeypatch):
    monkeypatch.setattr(db, "AsyncSessionLocal", async_session_factory)
    async def seed():
        async with async_session_factory() as session:
            session.add(User(id=uuid.uuid4(), email="weekly@example.com", hashed_password="test",
                             role="analyst", is_active=True, is_superuser=False, is_verified=True, wecom_userid="alice", wecom_alert_enabled=True))
            await session.commit()
    asyncio.run(seed())
    return async_session_factory


async def enqueue_test(queue):
    async with queue() as session:
        run = await jobs.enqueue(session, date(2026, 9, 15))
        return run.id


@pytest.mark.parametrize("fail", [False, True])
def test_end_to_end_queue_persists_before_agent_and_send(queue, monkeypatch, fail):
    async def agent(snapshot, agent_id, version, on_runtime, on_cleanup):
        async with queue() as session:
            job = (await session.execute(select(WorkBuddyReportJob))).scalar_one()
            run = await session.get(WeeklyReportRun, job.report_id)
            assert job.snapshot == snapshot and run.html_content
            assert job.state == "generating" and not run.wecom_sent
            # The producer must not hold an uncommitted write lock while waiting.
            await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            await session.execute(select(WorkBuddyReportJob).with_for_update())
        await on_runtime("123")
        await on_cleanup(True)
        if fail:
            raise provider.WorkBuddyError("invalid_output")
        return AgentReport.model_validate(OUTPUT)
    monkeypatch.setattr(provider, "generate", agent)
    sends = []
    monkeypatch.setattr(jobs, "send_report", lambda message, recipients: sends.append((message, recipients)) or "sent")
    async def run():
        report_id = await enqueue_test(queue)
        assert await enqueue_test(queue) == report_id
        assert await jobs.process_one()
        assert not sends
        async with queue() as session:
            job = await session.get(WorkBuddyReportJob, report_id)
            report = await session.get(WeeklyReportRun, report_id)
            assert job.state == "ready" and job.runtime_id == "123" and not job.cleanup_pending
            assert job.narrative_status == ("unavailable" if fail else "success")
            assert report.status == "success" and "WORKBUDDY_NARRATIVE" not in report.html_content
        assert await jobs.process_one()
        assert not await jobs.process_one()
        assert await enqueue_test(queue) == report_id
        assert not await jobs.process_one()
        async with queue() as session:
            assert (await session.get(WeeklyReportRun, report_id)).wecom_sent
    asyncio.run(run())
    assert len(sends) == 1 and sends[0][1] == ["alice"]
    assert "?page=reports&report=" in sends[0][0]


def test_concurrent_claims_have_one_owner(queue):
    async def run():
        await enqueue_test(queue)
        claims = await asyncio.gather(jobs._claim(), jobs._claim())
        assert sum(claim is not None for claim in claims) == 1
    asyncio.run(run())


@pytest.mark.parametrize("state,expected", [("preparing", "queued"), ("generating", "ready"), ("sending", "delivery_unknown")])
def test_recovery_does_not_repeat_agent_or_uncertain_send(queue, state, expected):
    async def run():
        report_id = await enqueue_test(queue)
        async with queue() as session:
            job = await session.get(WorkBuddyReportJob, report_id)
            job.state, job.lease_token, job.lease_until = state, uuid.uuid4(), jobs.now() - timedelta(minutes=1)
            report = await session.get(WeeklyReportRun, report_id)
            report.html_content = "<html><!--WORKBUDDY_NARRATIVE--></html>"
            report.notification_text = "已保存的数据摘要"
            await session.commit()
        await jobs.recover_expired()
        await enqueue_test(queue)
        async with queue() as session:
            job = await session.get(WorkBuddyReportJob, report_id)
            assert job.state == expected and job.lease_token is None
    asyncio.run(run())


@pytest.mark.parametrize("change", ["is_active=false", "wecom_alert_enabled=false"])
def test_recipient_revocation_blocks_pending_delivery(queue, monkeypatch, change):
    sends = []
    monkeypatch.setattr(jobs, "send_report", lambda *a: sends.append(a) or "sent")
    async def run():
        report_id = await enqueue_test(queue)
        async with queue() as session:
            job = await session.get(WorkBuddyReportJob, report_id)
            job.state = "ready"
            await session.execute(text('UPDATE "user" SET ' + change))
            await session.commit()
        await jobs.process_one()
        async with queue() as session:
            assert (await session.get(WorkBuddyReportJob, report_id)).state == "delivery_failed"
    asyncio.run(run())
    assert sends == []


def test_existing_recipient_override_is_reused_and_all_is_expanded(queue, monkeypatch):
    async def run():
        async with queue() as session:
            assert await delivery.configured_recipients(session) == ["alice"]
            await session.execute(text('UPDATE "user" SET wecom_alert_enabled=false'))
            await session.commit()
            with pytest.raises(ValueError):
                await delivery.configured_recipients(session)
            monkeypatch.setenv("WECOM_ALERT_TOUSER", "alice")
            assert await delivery.configured_recipients(session) == ["alice"]
            await delivery.validate_recipients(session, ["alice"])
            monkeypatch.setenv("WECOM_ALERT_TOUSER", "@all")
            assert await delivery.configured_recipients(session) == ["alice"]
            await session.execute(text('UPDATE "user" SET role=\'viewer\''))
            with pytest.raises(ValueError):
                await delivery.configured_recipients(session)
    asyncio.run(run())


def test_switching_to_legacy_does_not_bypass_unknown_delivery(queue, monkeypatch):
    from app.reports import service
    async def run():
        report_id = await enqueue_test(queue)
        async with queue() as session:
            report = await session.get(WeeklyReportRun, report_id)
            report.status, report.notification_text = "success", "saved"
            (await session.get(WorkBuddyReportJob, report_id)).state = "delivery_unknown"
            await session.commit()
        monkeypatch.setattr(settings, "report_provider", "legacy")
        monkeypatch.setattr(service, "send_wecom_alert", lambda *a: pytest.fail("must not send"))
        async with queue() as session:
            assert (await service.generate_weekly_report(session, date(2026, 9, 15))).id == report_id
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["failed", "unknown"])
def test_failed_or_uncertain_delivery_is_not_automatically_repeated(queue, monkeypatch, outcome):
    sends = []
    monkeypatch.setattr(jobs, "send_report", lambda *a: sends.append(a) or outcome)
    async def run():
        report_id = await enqueue_test(queue)
        async with queue() as session:
            (await session.get(WorkBuddyReportJob, report_id)).state = "ready"
            report = await session.get(WeeklyReportRun, report_id)
            report.status, report.notification_text = "success", "saved message"
            await session.commit()
        await jobs.process_one()
        await enqueue_test(queue)
        assert not await jobs.process_one()
        async with queue() as session:
            job = await session.get(WorkBuddyReportJob, report_id)
            assert job.state == "delivery_" + outcome
            assert job.delivery_attempts == 1
            assert not (await session.get(WeeklyReportRun, report_id)).wecom_sent
    asyncio.run(run())
    assert len(sends) == 1


def test_cloud_creation_network_failure_is_not_retried(config):
    from cloud_agent_sdk import CloudAgentClient
    requests = []
    def transport(request):
        requests.append(request)
        raise httpx.ReadTimeout("synthetic timeout")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            factory = lambda: CloudAgentClient(api_key="synthetic-key", source_app="rpa-test", retry=False, http_client=http)
            with pytest.raises(provider.WorkBuddyError, match="provider_failed"):
                await provider.generate(SNAPSHOT, "agent-test", "1.0", AsyncMock(), AsyncMock(), client_factory=factory)
    asyncio.run(run())
    assert len(requests) == 1

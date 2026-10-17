"""Authenticated queue and uncertain-delivery resolution, with worker disabled."""
import asyncio

from pydantic import SecretStr
from sqlalchemy import select

from app import db
from app.config import settings
from app.db.models import User, WorkBuddyReportJob, OperationLog
from test_api_endpoints import client, tokens


def setup_job(client, tokens, monkeypatch):
    # Switch only after TestClient startup; no cloud worker runs in these tests.
    monkeypatch.setattr(settings, "report_provider", "workbuddy")
    monkeypatch.setattr(settings, "workbuddy_api_key", SecretStr("synthetic"))
    monkeypatch.setattr(settings, "workbuddy_agent_id", "agent-test")
    monkeypatch.delenv("WECOM_ALERT_TOUSER", raising=False)
    async def bind():
        async with db.AsyncSessionLocal() as session:
            user = (await session.execute(select(User).where(User.email == "analyst@test.com"))).scalar_one()
            user.wecom_userid = "alice"
            user.wecom_alert_enabled = True
            await session.commit()
    asyncio.run(bind())
    headers = {"Authorization": "Bearer " + tokens["admin"]}
    result = client.post("/admin/reports/weekly/run", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["job_state"] == "queued"
    return result.json()["id"], headers


def test_queue_is_idempotent_and_visible_without_credentials(client, tokens, monkeypatch):
    report_id, headers = setup_job(client, tokens, monkeypatch)
    again = client.post("/admin/reports/weekly/run", headers=headers)
    assert again.json()["id"] == report_id
    result = client.get(f"/reports/weekly/{report_id}", headers=headers)
    assert result.json()["job_state"] == "queued"
    assert "agent_id" not in result.json() and "snapshot" not in result.json()
    assert client.get(f"/reports/weekly/{report_id}").status_code == 401
    analyst = {"Authorization": "Bearer " + tokens["analyst"]}
    assert client.post("/admin/reports/weekly/run", headers=analyst).status_code == 403


def test_uncertain_delivery_requires_acknowledgement_and_records_admin_action(client, tokens, monkeypatch):
    report_id, headers = setup_job(client, tokens, monkeypatch)
    async def ambiguous():
        async with db.AsyncSessionLocal() as session:
            job = await session.get(WorkBuddyReportJob, report_id)
            job.state, job.delivery_error = "delivery_unknown", "请核对是否收到。"
            await session.commit()
    asyncio.run(ambiguous())
    path = f"/admin/reports/weekly/{report_id}/delivery"
    assert client.post(path, json={"action": "retry"}, headers=headers).status_code == 409
    analyst = {"Authorization": "Bearer " + tokens["analyst"]}
    assert client.post(path, json={"action": "mark_sent", "acknowledged_uncertain_delivery": True}, headers=analyst).status_code == 403
    result = client.post(path, json={"action": "mark_sent", "acknowledged_uncertain_delivery": True}, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["wecom_sent"] and result.json()["job_state"] == "sent"
    assert result.json()["delivery_error"] is None
    assert client.post(path, json={"action": "retry"}, headers=headers).status_code == 409
    async def audit():
        async with db.AsyncSessionLocal() as session:
            assert (await session.execute(select(OperationLog).where(OperationLog.action == "workbuddy_delivery_action"))).scalar_one()
    asyncio.run(audit())


def test_failed_job_of_any_week_can_be_regenerated(client, tokens, monkeypatch):
    report_id, headers = setup_job(client, tokens, monkeypatch)
    path = f"/admin/reports/weekly/{report_id}/delivery"
    assert client.post(path, json={"action": "regenerate"}, headers=headers).status_code == 409
    async def fail():
        async with db.AsyncSessionLocal() as session:
            job = await session.get(WorkBuddyReportJob, report_id)
            job.state, job.prepare_attempts = "failed", 3
            await session.commit()
    asyncio.run(fail())
    result = client.post(path, json={"action": "regenerate"}, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["job_state"] == "queued" and result.json()["worker_enabled"] is True
    monkeypatch.setattr(settings, "report_provider", "legacy")
    listed = client.get("/reports/weekly", headers=headers).json()
    assert listed[0]["worker_enabled"] is False

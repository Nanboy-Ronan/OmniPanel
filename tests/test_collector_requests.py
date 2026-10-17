"""On-demand collect queue: queued by repair/admin, executed by the collector unit."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from test_api_endpoints import client, tokens
from test_collector_repair import approved, auth, setup, upload, collector_dir  # noqa: F401
from app import db
from app.config import settings
from app.collector import collect_requests
from app.db.models import CollectorRequest, CollectorRun


@pytest.fixture
def collecting(monkeypatch):
    monkeypatch.setattr(settings, "collector_enabled", True)
    monkeypatch.setattr(settings, "collector_xhs_enabled", True)
    monkeypatch.setattr(settings, "collector_channels_enabled", False)


def requests_rows():
    async def read():
        async with db.AsyncSessionLocal() as session:
            return (await session.execute(select(CollectorRequest).order_by(CollectorRequest.id))).scalars().all()
    return asyncio.run(read())


def test_successful_repair_queues_one_collect_and_coalesces(client, tokens, collecting):
    acc = setup(client, tokens)
    for _ in range(2):
        pair = approved(client, tokens, acc)
        with patch('app.collector.repair.verify_candidate', return_value=True):
            body = upload(client, pair).json()
        assert body == {"status": "success", "collect_queued": True}
    rows = requests_rows()
    assert [(r.platform, r.account_id, r.reason, r.status) for r in rows] == [("xhs", acc['id'], "repair", "queued")]
    targets = client.get('/collector/repair/targets', headers=auth(tokens['analyst'])).json()
    assert targets[0]['collect_pending'] is True


def test_no_queue_when_collecting_is_off(client, tokens, monkeypatch):
    monkeypatch.setattr(settings, "collector_enabled", False)
    acc = setup(client, tokens)
    pair = approved(client, tokens, acc)
    with patch('app.collector.repair.verify_candidate', return_value=True):
        assert upload(client, pair).json() == {"status": "success", "collect_queued": False}
    assert requests_rows() == []


def test_runner_executes_skips_and_recovers(client, collecting):
    now = datetime.now(timezone.utc)
    def seed(session):
        session.add_all([
            CollectorRequest(platform="xhs", account_id=1, reason="repair"),
            CollectorRequest(platform="xhs", account_id=2, reason="admin"),
            CollectorRequest(platform="xhs", account_id=3, reason="repair", created_at=now - timedelta(hours=7)),
            CollectorRequest(platform="channels", account_id=4, reason="admin"),
            CollectorRequest(platform="xhs", account_id=5, reason="admin", status="running",
                             started_at=now - timedelta(hours=2)),
        ])
        session.commit()
    with db.SyncSessionLocal() as session:
        seed(session)
    calls = []
    def runner(**kwargs):
        calls.append((kwargs["triggered_by"], kwargs["only_platform"], kwargs["only_account_id"]))
        return 0 if kwargs["only_account_id"] == 1 else 1
    assert collect_requests.run_requests(runner=runner) == 1
    assert calls == [("request", "xhs", 1), ("request", "xhs", 2)]
    states = {r.account_id: r.status for r in requests_rows()}
    assert states == {1: "done", 2: "failed", 3: "skipped", 4: "skipped", 5: "failed"}


def test_runner_closes_request_when_interrupted(client, collecting):
    from app.collector.runner import CollectorInterrupted
    with db.SyncSessionLocal() as session:
        session.add(CollectorRequest(platform="xhs", account_id=1, reason="admin"))
        session.commit()
    def runner(**kwargs):
        raise CollectorInterrupted("signal 15")
    with pytest.raises(CollectorInterrupted):
        collect_requests.run_requests(runner=runner)
    assert requests_rows()[0].status == "failed"


def test_admin_can_request_a_collect(client, tokens, collecting):
    acc = setup(client, tokens)
    path = '/admin/collector/requests'
    assert client.post(path, json={"platform": "xhs", "account_id": acc['id']}, headers=auth(tokens['analyst'])).status_code == 403
    assert client.post(path, json={"platform": "xhs"}, headers=auth(tokens['admin'])).status_code == 422
    assert client.post(path, json={"platform": "zhihu", "account_id": 1}, headers=auth(tokens['admin'])).status_code == 422
    assert client.post(path, json={"platform": "channels", "account_id": 1}, headers=auth(tokens['admin'])).status_code == 409
    assert client.post(path, json={"platform": "xhs", "account_id": acc['id']}, headers=auth(tokens['admin'])).status_code == 202
    assert client.post(path, json={"platform": "xhs", "account_id": acc['id']}, headers=auth(tokens['admin'])).status_code == 202
    listed = client.get(path, headers=auth(tokens['admin'])).json()
    assert [(r["platform"], r["reason"], r["status"]) for r in listed] == [("xhs", "admin", "queued")]


def test_readiness_flags_a_queue_nobody_processes(client, tokens, monkeypatch):
    monkeypatch.setattr('app.views.collector_repair._browser_ready', lambda: True)
    with db.SyncSessionLocal() as session:
        session.add(CollectorRequest(platform="xhs", account_id=1, reason="admin",
                                     created_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
        session.commit()
    body = client.get('/collector/repair/readiness', headers=auth(tokens['admin'])).json()
    assert body['requests_stalled'] is True


def test_request_runs_count_as_data_freshness():
    from app.collector.runs import LOGIN_CHECK_TRIGGERS
    assert collect_requests.TRIGGER not in LOGIN_CHECK_TRIGGERS
    assert len(collect_requests.TRIGGER) <= CollectorRun.triggered_by.type.length

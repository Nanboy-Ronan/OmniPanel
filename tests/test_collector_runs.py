"""Tests for CollectorRun bookkeeping (app/collector/runs.py).

No HTTP, no browser — just start_run/finish_run against a real test DB via
the same SyncSessionLocal-monkeypatch pattern used by test_upload_background.py.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def sync_db(pg_sync_url, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    import app.db as db

    engine = create_engine(pg_sync_url, future=True, echo=False)
    SyncSL = sessionmaker(engine, class_=Session, expire_on_commit=False)
    monkeypatch.setattr(db, "SyncSessionLocal", SyncSL, raising=False)
    yield SyncSL
    engine.dispose()


def _get_run(SyncSL, run_id: int):
    from app.db.models import CollectorRun
    with SyncSL() as s:
        return s.get(CollectorRun, run_id)


class TestStartFinishRun:
    def test_start_run_creates_running_row(self, sync_db):
        from app.collector.runs import start_run

        run_id = start_run("xhs", account_id=3, triggered_by="manual")
        run = _get_run(sync_db, run_id)
        assert run is not None
        assert run.platform == "xhs"
        assert run.account_id == 3
        assert run.status == "running"
        assert run.triggered_by == "manual"
        assert run.finished_at is None

    def test_finish_run_success_records_rows(self, sync_db):
        from app.collector.runs import start_run, finish_run

        run_id = start_run("zhihu", content_type="article")
        finish_run(run_id, "success", rows_upserted=12, filename="export.csv")
        run = _get_run(sync_db, run_id)
        assert run.status == "success"
        assert run.rows_upserted == 12
        assert run.filename == "export.csv"
        assert run.finished_at is not None

    def test_finish_run_records_error(self, sync_db):
        from app.collector.runs import start_run, finish_run

        run_id = start_run("xhs", account_id=1)
        finish_run(run_id, "session_expired", error_message="redirected to login")
        run = _get_run(sync_db, run_id)
        assert run.status == "session_expired"
        assert run.error_message == "redirected to login"

    def test_zhihu_run_has_no_account_id(self, sync_db):
        from app.collector.runs import start_run

        run_id = start_run("zhihu", content_type="qa")
        run = _get_run(sync_db, run_id)
        assert run.account_id is None
        assert run.content_type == "qa"

    def test_finish_run_on_missing_id_is_noop(self, sync_db):
        from app.collector.runs import finish_run

        # Should not raise even though no such run exists.
        finish_run(999999, "success")

    def test_default_triggered_by_is_schedule(self, sync_db):
        from app.collector.runs import start_run

        run_id = start_run("xhs", account_id=1)
        run = _get_run(sync_db, run_id)
        assert run.triggered_by == "schedule"

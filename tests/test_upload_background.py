"""Tests for background upload processing (Item 1).

POST /upload/ now returns 202 immediately with a batch_id. The heavy ETL
(detect + normalize + insert) runs in a FastAPI BackgroundTask. Clients poll
GET /upload/batches/{id} to learn the final status.

Starlette's TestClient executes BackgroundTasks synchronously before returning
from client.post(), so tests can poll the batch endpoint right away without
any sleeps.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_VALID_CSV = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,"
    "全部商品名称,商品种类数,订单实付金额\n"
    "BG001,2025-03-01,13900001111,Widget,1,49.00\n"
)

_MISSING_PLATFORM_CSV = "订单号,买家付款时间,全部商品名称\n1,2025-01-01,item"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(pg_async_url, monkeypatch):
    from sqlalchemy import create_engine as _sync_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker, Session
    import app.db as db

    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SL = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    sync_url = pg_async_url.replace("+asyncpg", "+psycopg2")
    s_engine = _sync_engine(sync_url, poolclass=NullPool)
    SyncSL = sessionmaker(s_engine, class_=Session, expire_on_commit=False)

    monkeypatch.setattr(db, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", SL, raising=False)
    monkeypatch.setattr(db, "SyncSessionLocal", SyncSL, raising=False)

    import app.main
    importlib.reload(app.main)

    from fastapi.testclient import TestClient
    with TestClient(app.main.app) as c:
        yield c

    import asyncio
    asyncio.run(engine.dispose())
    s_engine.dispose()


@pytest.fixture
def admin_token(client):
    client.post("/auth/register", json={"email": "bgtest@test.com", "password": "pw"})
    r = client.post("/auth/jwt/login", data={"username": "bgtest@test.com", "password": "pw"})
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload(client, token, csv_bytes, name="orders.csv"):
    return client.post(
        "/upload/",
        files={"file": (name, csv_bytes, "text/csv")},
        headers=_auth(token),
    )


def _batch(client, token, batch_id):
    return client.get(f"/upload/batches/{batch_id}", headers=_auth(token))


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestBackgroundUploadResponse:

    def test_valid_upload_returns_202(self, client, admin_token):
        r = _upload(client, admin_token, _VALID_CSV.encode())
        assert r.status_code == 202, r.text

    def test_response_contains_batch_id_and_status(self, client, admin_token):
        r = _upload(client, admin_token, _VALID_CSV.encode())
        body = r.json()
        assert "batch_id" in body, body
        assert isinstance(body["batch_id"], int)
        assert body["status"] == "processing"

    def test_unauthenticated_upload_still_rejected_synchronously(self, client):
        r = client.post("/upload/", files={"file": ("x.csv", _VALID_CSV.encode())})
        assert r.status_code in (401, 403)

    def test_bad_extension_still_rejected_synchronously(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("evil.py", _VALID_CSV.encode(), "text/plain")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 400

    def test_oversized_file_still_rejected_synchronously(self, client, admin_token, monkeypatch):
        import app.views.ecommerce.upload as mod
        monkeypatch.setattr(mod, "_MAX_UPLOAD_BYTES", 10)
        r = _upload(client, admin_token, b"x" * 20)
        assert r.status_code == 413


class TestBatchStatusPolling:

    def test_valid_upload_batch_completes(self, client, admin_token):
        r = _upload(client, admin_token, _VALID_CSV.encode())
        assert r.status_code == 202
        batch_id = r.json()["batch_id"]

        r2 = _batch(client, admin_token, batch_id)
        assert r2.status_code == 200
        b = r2.json()
        assert b["status"] == "completed"
        assert b["inserted_orders"] == 1
        assert b["platform"] == "youzan"
        assert b["row_count"] == 1

    def test_bad_csv_batch_fails(self, client, admin_token):
        r = _upload(client, admin_token, _MISSING_PLATFORM_CSV.encode(), name="bad.csv")
        assert r.status_code == 202
        batch_id = r.json()["batch_id"]

        r2 = _batch(client, admin_token, batch_id)
        assert r2.status_code == 200
        b = r2.json()
        assert b["status"] == "failed"
        assert b["error_message"] is not None
        assert len(b["error_message"]) > 0

    def test_batch_status_requires_auth(self, client, admin_token):
        r = _upload(client, admin_token, _VALID_CSV.encode())
        batch_id = r.json()["batch_id"]
        r2 = client.get(f"/upload/batches/{batch_id}")
        assert r2.status_code in (401, 403)

    def test_unknown_batch_id_returns_404(self, client, admin_token):
        r = client.get("/upload/batches/9999999", headers=_auth(admin_token))
        assert r.status_code == 404

    def test_duplicate_upload_reports_zero_inserted(self, client, admin_token):
        for _ in range(2):
            r = _upload(client, admin_token, _VALID_CSV.encode())
            assert r.status_code == 202

        batch_id = r.json()["batch_id"]
        r2 = _batch(client, admin_token, batch_id)
        b = r2.json()
        assert b["status"] == "completed"
        assert b["inserted_orders"] == 0
        assert b["duplicate_rows"] == 1

    def test_batch_error_message_absent_on_success(self, client, admin_token):
        r = _upload(client, admin_token, _VALID_CSV.encode())
        batch_id = r.json()["batch_id"]
        b = _batch(client, admin_token, batch_id).json()
        assert b["status"] == "completed"
        assert b.get("error_message") is None

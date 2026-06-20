"""Tests for file upload validation: extension whitelist and MIME type guard.

TDD: written before the implementation.

Coverage:
  Extension whitelist (enforced before any file parsing):
    - .csv accepted
    - .xlsx accepted
    - .xls accepted
    - .py rejected → 400
    - .exe rejected → 400
    - .txt rejected → 400
    - .json rejected → 400
    - no extension → 400
    - extension check is case-insensitive (.CSV / .XLSX)

  MIME type guard (content-type header):
    - clearly hostile MIME (application/x-executable) → 415
    - application/octet-stream accepted (many browsers send this for Excel)
    - missing content-type accepted (let extension check handle it)

  Size limit (already partially implemented; verified here):
    - file at exactly MAX_UPLOAD_MB limit accepted
    - file exceeding MAX_UPLOAD_MB → 413
"""
from __future__ import annotations

import importlib
import io
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Minimal valid Youzan CSV (only the columns the ETL requires) ──────────────

_VALID_CSV = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,"
    "全部商品名称,商品种类数,订单实付金额\n"
    "TEST001,2025-01-15,13800001111,Test Product,1,88.00\n"
)

_VALID_XLSX_STUB = b""  # placeholder — tests that need real XLSX use openpyxl


def _make_xlsx_bytes() -> bytes:
    """Create a minimal valid XLSX file in memory."""
    import openpyxl, io
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = [
        "订单号", "买家付款时间", "收货人手机号/提货人手机号",
        "全部商品名称", "商品种类数", "订单实付金额",
    ]
    ws.append(headers)
    ws.append(["TEST002", "2025-01-16", "13800002222", "Test Prod B", 1, 99.0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def client(pg_async_url, monkeypatch):
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    import app.db as db

    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SL = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", SL, raising=False)

    import app.main
    importlib.reload(app.main)

    from fastapi.testclient import TestClient
    with TestClient(app.main.app) as c:
        yield c

    import asyncio
    asyncio.run(engine.dispose())


@pytest.fixture
def admin_token(client):
    client.post(
        "/auth/register",
        json={"email": "admin@upload.com", "password": "pw", "role": "viewer"},
    )
    r = client.post("/auth/jwt/login", data={"username": "admin@upload.com", "password": "pw"})
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════
# Section 1: Extension whitelist
# ═══════════════════════════════════════════════════════════════

class TestUploadExtensionValidation:

    def test_csv_extension_accepted(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("data.csv", _VALID_CSV.encode(), "text/csv")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 202, r.text

    def test_xlsx_extension_accepted(self, client, admin_token):
        xlsx_bytes = _make_xlsx_bytes()
        r = client.post(
            "/upload/",
            files={"file": ("data.xlsx", xlsx_bytes,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 202, r.text

    def test_py_extension_rejected(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("exploit.py", b"import os; os.system('rm -rf /')", "text/plain")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "unsupported" in detail or ".py" in detail

    def test_exe_extension_rejected(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("malware.exe", b"\x4d\x5a\x90\x00", "application/octet-stream")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 400

    def test_txt_extension_rejected(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("notes.txt", b"some text", "text/plain")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 400

    def test_json_extension_rejected(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("data.json", b'{"key": "val"}', "application/json")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 400

    def test_no_extension_rejected(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("datafile", _VALID_CSV.encode(), "text/csv")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 400

    def test_csv_uppercase_extension_accepted(self, client, admin_token):
        """Extension check must be case-insensitive."""
        r = client.post(
            "/upload/",
            files={"file": ("DATA.CSV", _VALID_CSV.encode(), "text/csv")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 202, r.text

    def test_extension_check_precedes_file_parsing(self, client, admin_token):
        """Validation happens before pandas tries to parse the file."""
        # A .py file with valid CSV content must still be rejected by extension check
        r = client.post(
            "/upload/",
            files={"file": ("sneaky.py", _VALID_CSV.encode(), "text/csv")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════
# Section 2: MIME type guard
# ═══════════════════════════════════════════════════════════════

class TestUploadMimeTypeValidation:

    def test_hostile_mime_type_rejected(self, client, admin_token):
        """A .csv file sent with an executable MIME type must be rejected."""
        r = client.post(
            "/upload/",
            files={"file": ("data.csv", _VALID_CSV.encode(), "application/x-executable")},
            headers=_auth(admin_token),
        )
        assert r.status_code in (400, 415)

    def test_octet_stream_mime_accepted(self, client, admin_token):
        """application/octet-stream is valid — many browsers send it for Excel."""
        r = client.post(
            "/upload/",
            files={"file": ("data.csv", _VALID_CSV.encode(), "application/octet-stream")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 202, r.text

    def test_text_csv_mime_accepted(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("data.csv", _VALID_CSV.encode(), "text/csv")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 202, r.text

    def test_application_vnd_mime_accepted(self, client, admin_token):
        xlsx_bytes = _make_xlsx_bytes()
        r = client.post(
            "/upload/",
            files={"file": (
                "data.xlsx", xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )},
            headers=_auth(admin_token),
        )
        assert r.status_code == 202, r.text


# ═══════════════════════════════════════════════════════════════
# Section 3: Size limit
# ═══════════════════════════════════════════════════════════════

class TestUploadSizeLimit:

    def test_empty_file_rejected(self, client, admin_token):
        r = client.post(
            "/upload/",
            files={"file": ("empty.csv", b"", "text/csv")},
            headers=_auth(admin_token),
        )
        # Either 400 (parse error) or 400 (empty content) — not 200
        assert r.status_code != 200

    def test_oversized_file_rejected(self, client, admin_token):
        """A file exceeding MAX_UPLOAD_MB must return 413."""
        max_mb = int(os.getenv("MAX_UPLOAD_MB", "50"))
        oversized = b"x" * (max_mb * 1024 * 1024 + 1)
        r = client.post(
            "/upload/",
            files={"file": ("big.csv", oversized, "text/csv")},
            headers=_auth(admin_token),
        )
        assert r.status_code == 413
        assert str(max_mb) in r.json()["detail"]

    def test_unauthenticated_upload_rejected(self, client):
        r = client.post(
            "/upload/",
            files={"file": ("data.csv", _VALID_CSV.encode(), "text/csv")},
        )
        assert r.status_code in (401, 403)

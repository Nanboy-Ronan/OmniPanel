"""Backend field-coverage integration tests. Frontend dictionary tests live in frontend/src/test."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ─── Shared constants ─────────────────────────────────────────────────────────

EXPECTED_TABLES = {"orders", "customers", "upload_batches"}

EXPECTED_ORDERS_FIELDS = {
    "id", "order_date", "order_id", "customer_key", "platform",
    "sku", "quantity", "price", "receiver", "receiver_phone",
    "province", "area", "full_address", "buyer_nick", "coupon_name", "distributor",
}

ORDERS_NULLABLE_FIELDS = {
    "sku", "quantity", "price", "receiver", "receiver_phone",
    "province", "area", "full_address", "buyer_nick", "coupon_name", "distributor",
}

ORDERS_NON_NULLABLE_FIELDS = EXPECTED_ORDERS_FIELDS - ORDERS_NULLABLE_FIELDS

EXPECTED_DF_COLUMNS = {
    "字段名", "中文说明", "类型", "示例值",
    "有赞原始列", "京东原始列", "天猫原始列", "非 null 覆盖率",
}


@pytest.fixture
def client(pg_async_url, monkeypatch):
    from sqlalchemy import create_engine as _sync_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker, Session
    import app.db as db

    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # The upload ingestion thread uses the sync session; point it at the test DB.
    sync_url = pg_async_url.replace("+asyncpg", "+psycopg2")
    s_engine = _sync_engine(sync_url, poolclass=NullPool)
    SyncSL = sessionmaker(s_engine, class_=Session, expire_on_commit=False)

    monkeypatch.setattr(db, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", SessionLocal, raising=False)
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
def tokens(client):
    client.post("/auth/register", json={"email": "first@test.com", "password": "pw"})
    r = client.post("/auth/jwt/login", data={"username": "first@test.com", "password": "pw"})
    admin_token = r.json()["access_token"]

    def _create(email, role):
        r2 = client.post(
            "/admin/users",
            json={"email": email, "password": "pw", "role": role},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r2.status_code == 201, r2.text
        r3 = client.post("/auth/jwt/login", data={"username": email, "password": "pw"})
        return r3.json()["access_token"]

    return {role: _create(f"{role}@test.com", role) for role in ["viewer", "analyst", "admin"]}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Seed CSV fixtures ──────────────────────────────────────────────────────────

# Minimal youzan CSV — only required columns.
# Absent optional columns (receiver, province, area, full_address,
# buyer_nick, coupon_name, distributor) are stored as NULL in the DB.
# receiver_phone IS present (same column used for customer_key in youzan).
_MINIMAL_CSV = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
    "5001,2025-07-01,13800001111,SKU A,1,99.00\n"
    "5002,2025-07-02,13800002222,SKU B,2,198.00\n"
)

# Full youzan CSV — all optional fields populated.
_FULL_CSV = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额,"
    "收货人/提货人,收货人省份,收货人地区,详细收货地址/提货地址,买家昵称,优惠券码名称,分销员\n"
    "6001,2025-07-01,13900001111,SKU X,1,199.00,"
    "张三,广东省,深圳市,广东省深圳市南山区XX路,user_abc,满200减50,导购小李\n"
    "6002,2025-07-02,13900002222,SKU Y,1,299.00,"
    "李四,北京市,朝阳区,北京市朝阳区XX路,user_def,满100减20,导购小王\n"
)

# Partial CSV — two rows, only first has coupon_name → coupon coverage = 0.5.
_PARTIAL_COUPON_CSV = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额,优惠券码名称\n"
    "7001,2025-07-01,13700001111,SKU P,1,50.00,满50减10\n"
    "7002,2025-07-02,13700002222,SKU Q,1,80.00,\n"
)


# ── Access-control ─────────────────────────────────────────────────────────────

class TestFieldCoveragePermissions:
    def test_unauthenticated_returns_401_or_403(self, client):
        r = client.get("/analysis/field_coverage")
        assert r.status_code in (401, 403)

    def test_viewer_is_forbidden(self, client, tokens):
        r = client.get("/analysis/field_coverage", headers=_auth(tokens["viewer"]))
        assert r.status_code == 403

    def test_analyst_can_access(self, client, tokens):
        r = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"]))
        assert r.status_code == 200

    def test_admin_can_access(self, client, tokens):
        r = client.get("/analysis/field_coverage", headers=_auth(tokens["admin"]))
        assert r.status_code == 200


# ── Empty database ─────────────────────────────────────────────────────────────

class TestFieldCoverageEmptyDb:
    def test_total_rows_is_zero(self, client, tokens):
        r = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"]))
        assert r.status_code == 200
        assert r.json()["total_rows"] == 0

    def test_columns_dict_is_empty(self, client, tokens):
        r = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"]))
        assert r.json()["columns"] == {}

    def test_response_always_has_required_keys(self, client, tokens):
        body = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()
        assert "total_rows" in body
        assert "columns" in body


# ── With minimal data ──────────────────────────────────────────────────────────

class TestFieldCoverageMinimalData:
    @pytest.fixture(autouse=True)
    def _seed(self, client, tokens):
        r = client.post(
            "/upload/",
            files={"file": ("seed.csv", _MINIMAL_CSV)},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 202, r.text

    def test_total_rows_equals_uploaded_count(self, client, tokens):
        r = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"]))
        assert r.json()["total_rows"] == 2

    def test_columns_contains_exactly_nullable_fields(self, client, tokens):
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        assert set(cols.keys()) == ORDERS_NULLABLE_FIELDS

    def test_all_coverage_values_are_floats_in_range(self, client, tokens):
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        for col, rate in cols.items():
            assert isinstance(rate, (int, float)), f"{col}: rate is not numeric"
            assert 0.0 <= rate <= 1.0, f"{col}: rate {rate} is out of [0.0, 1.0]"

    def test_sku_price_quantity_are_fully_covered(self, client, tokens):
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        assert cols["sku"] == 1.0
        assert cols["price"] == 1.0
        assert cols["quantity"] == 1.0

    def test_receiver_phone_is_fully_covered(self, client, tokens):
        """Youzan uses the phone column for both customer_key and receiver_phone."""
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        assert cols["receiver_phone"] == 1.0

    def test_absent_optional_fields_have_zero_coverage(self, client, tokens):
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        for field in ("receiver", "province", "area", "full_address",
                      "buyer_nick", "coupon_name", "distributor"):
            assert cols[field] == 0.0, f"Expected 0.0 for {field}, got {cols[field]}"


# ── With fully-populated data ──────────────────────────────────────────────────

class TestFieldCoverageFullData:
    @pytest.fixture(autouse=True)
    def _seed(self, client, tokens):
        r = client.post(
            "/upload/",
            files={"file": ("full.csv", _FULL_CSV)},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 202, r.text

    def test_total_rows_equals_uploaded_count(self, client, tokens):
        r = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"]))
        assert r.json()["total_rows"] == 2

    def test_all_populated_nullable_fields_are_fully_covered(self, client, tokens):
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        for field in ("sku", "price", "receiver", "receiver_phone", "province",
                      "area", "full_address", "buyer_nick", "coupon_name", "distributor"):
            assert cols[field] == 1.0, f"Expected 1.0 for {field}, got {cols[field]}"


# ── Partial coverage ───────────────────────────────────────────────────────────

class TestFieldCoveragePartialData:
    """Two rows: first has coupon_name, second does not → rate should be 0.5."""

    @pytest.fixture(autouse=True)
    def _seed(self, client, tokens):
        r = client.post(
            "/upload/",
            files={"file": ("partial.csv", _PARTIAL_COUPON_CSV)},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 202, r.text

    def test_coupon_name_coverage_is_half(self, client, tokens):
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        assert cols["coupon_name"] == pytest.approx(0.5, abs=0.01)

    def test_fully_present_fields_still_show_full_coverage(self, client, tokens):
        cols = client.get("/analysis/field_coverage", headers=_auth(tokens["analyst"])).json()["columns"]
        assert cols["sku"] == 1.0
        assert cols["price"] == 1.0

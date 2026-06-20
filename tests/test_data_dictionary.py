"""Tests for feature #4: Data Dictionary

Coverage plan
─────────────
Unit tests (no DB / no HTTP):
  data_dictionary._build_df():
  - returns a DataFrame with the expected 8 columns
  - returns correct row count for each table (orders, customers, upload_batches)
  - all expected orders field names are present in the output
  - empty search string returns all rows
  - search matches on field name (case-insensitive)
  - search matches on Chinese description
  - search matches on example value
  - search with no matches returns an empty DataFrame
  - nullable fields with coverage display the correct percentage string
  - nullable fields without coverage display "—"
  - non-nullable fields display "N/A" regardless of the coverage dict
  - 0 % and 100 % edge cases are formatted correctly
  - unexpected keys in coverage dict do not raise

  Static _FIELD_DEFS sanity checks:
  - all three tables are present
  - orders table has exactly the expected set of field names
  - every field has all required metadata keys
  - nullable flag is a bool on every field
  - non-nullable orders fields are correctly flagged
  - nullable orders fields are correctly flagged
  - Chinese description and example value are non-empty on every field

Integration tests (FastAPI TestClient + PostgreSQL):
  GET /analysis/field_coverage:
  - unauthenticated → 401 or 403
  - viewer role → 403
  - analyst role → 200
  - admin role → 200
  - empty database → {"total_rows": 0, "columns": {}}
  - response has "total_rows" and "columns" keys at all times
  - with data: total_rows equals uploaded order count
  - with data: "columns" contains exactly the expected nullable column names
  - with data: every coverage value is a float in [0.0, 1.0]
  - minimal CSV (only required columns) → always-present fields have rate 1.0,
    absent optional fields (buyer_nick, coupon_name, distributor) have rate 0.0
  - full CSV (all optional columns populated) → all covered fields have rate 1.0
  - partial CSV (one row has coupon, one does not) → coupon_name rate ≈ 0.5
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
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


def _import_dict_module():
    from app.ui.pages.data_dictionary import _build_df, _FIELD_DEFS
    return _build_df, _FIELD_DEFS


# ═════════════════════════════════════════════════════════════════════════════
# Section 1: Unit tests — _build_df()
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildDfStructure:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.build_df, self.field_defs = _import_dict_module()

    def test_returns_dataframe(self):
        assert isinstance(self.build_df("orders", None), pd.DataFrame)

    def test_has_exactly_the_expected_columns(self):
        df = self.build_df("orders", None)
        assert set(df.columns) == EXPECTED_DF_COLUMNS

    def test_orders_row_count_matches_field_defs(self):
        df = self.build_df("orders", None)
        assert len(df) == len(self.field_defs["orders"])

    def test_customers_row_count_matches_field_defs(self):
        df = self.build_df("customers", None)
        assert len(df) == len(self.field_defs["customers"])

    def test_upload_batches_row_count_matches_field_defs(self):
        df = self.build_df("upload_batches", None)
        assert len(df) == len(self.field_defs["upload_batches"])

    def test_all_expected_orders_fields_are_present(self):
        df = self.build_df("orders", None)
        assert set(df["字段名"]) == EXPECTED_ORDERS_FIELDS


class TestBuildDfSearch:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.build_df, _ = _import_dict_module()

    def test_empty_search_returns_all_rows(self):
        assert len(self.build_df("orders", None, "")) == len(self.build_df("orders", None))

    def test_search_by_exact_field_name(self):
        df = self.build_df("orders", None, "sku")
        assert len(df) >= 1
        assert all("sku" in f.lower() for f in df["字段名"])

    def test_search_by_chinese_description(self):
        df = self.build_df("orders", None, "省份")
        assert len(df) >= 1
        assert any("省份" in d for d in df["中文说明"])

    def test_search_by_example_value(self):
        df = self.build_df("orders", None, "199")
        assert len(df) >= 1

    def test_search_is_case_insensitive(self):
        assert len(self.build_df("orders", None, "sku")) == len(
            self.build_df("orders", None, "SKU")
        )

    def test_search_no_match_returns_empty_df(self):
        df = self.build_df("orders", None, "xyz_nonexistent_zzzzzz")
        assert len(df) == 0
        assert set(df.columns) == EXPECTED_DF_COLUMNS

    def test_search_platform_field_works_across_all_tables(self):
        for table in ("orders", "customers", "upload_batches"):
            df = self.build_df(table, None, "platform")
            assert len(df) >= 1, f"Expected at least 1 match for 'platform' in {table}"


class TestBuildDfCoverage:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.build_df, _ = _import_dict_module()

    def _row(self, field: str, coverage=None) -> pd.Series:
        df = self.build_df("orders", coverage)
        return df[df["字段名"] == field].iloc[0]

    def test_nullable_with_coverage_shows_percentage(self):
        assert self._row("sku", {"sku": 0.95})["非 null 覆盖率"] == "95.0%"

    def test_nullable_zero_coverage(self):
        assert self._row("coupon_name", {"coupon_name": 0.0})["非 null 覆盖率"] == "0.0%"

    def test_nullable_full_coverage(self):
        assert self._row("distributor", {"distributor": 1.0})["非 null 覆盖率"] == "100.0%"

    def test_nullable_without_coverage_shows_dash(self):
        for field in ORDERS_NULLABLE_FIELDS:
            val = self._row(field, None)["非 null 覆盖率"]
            assert val == "—", f"{field}: expected '—', got {val!r}"

    def test_non_nullable_always_shows_na(self):
        for field in ORDERS_NON_NULLABLE_FIELDS:
            val = self._row(field, None)["非 null 覆盖率"]
            assert "N/A" in val, f"{field}: expected 'N/A' marker, got {val!r}"

    def test_non_nullable_shows_na_even_when_coverage_dict_contains_it(self):
        coverage = {"id": 1.0, "order_id": 1.0, "platform": 1.0}
        for field in ORDERS_NON_NULLABLE_FIELDS:
            val = self._row(field, coverage)["非 null 覆盖率"]
            assert "N/A" in val, f"{field}: must show N/A regardless of coverage dict"

    def test_extra_keys_in_coverage_dict_do_not_raise(self):
        coverage = {"nonexistent_column": 0.5, "sku": 0.8}
        df = self.build_df("orders", coverage)
        assert len(df) > 0


class TestFieldDefsContent:
    """Sanity-check the static data-dictionary definitions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        _, self.field_defs = _import_dict_module()

    def test_all_expected_tables_present(self):
        assert EXPECTED_TABLES.issubset(self.field_defs.keys())

    def test_orders_has_exactly_expected_fields(self):
        assert set(self.field_defs["orders"].keys()) == EXPECTED_ORDERS_FIELDS

    def test_every_field_has_required_metadata_keys(self):
        required = {"zh", "type", "example", "youzan", "jd", "tmall", "nullable"}
        for table, fields in self.field_defs.items():
            for field, meta in fields.items():
                missing = required - meta.keys()
                assert not missing, f"{table}.{field} missing keys: {missing}"

    def test_nullable_is_always_a_bool(self):
        for table, fields in self.field_defs.items():
            for field, meta in fields.items():
                assert isinstance(meta["nullable"], bool), (
                    f"{table}.{field}.nullable must be bool, got {type(meta['nullable'])}"
                )

    def test_non_nullable_orders_fields_flagged_correctly(self):
        for field in ORDERS_NON_NULLABLE_FIELDS:
            assert not self.field_defs["orders"][field]["nullable"], (
                f"orders.{field} should be non-nullable"
            )

    def test_nullable_orders_fields_flagged_correctly(self):
        for field in ORDERS_NULLABLE_FIELDS:
            assert self.field_defs["orders"][field]["nullable"], (
                f"orders.{field} should be nullable"
            )

    def test_chinese_description_is_non_empty_for_all_fields(self):
        for table, fields in self.field_defs.items():
            for field, meta in fields.items():
                assert meta["zh"].strip(), f"{table}.{field} has an empty Chinese description"

    def test_example_value_is_non_empty_for_all_fields(self):
        for table, fields in self.field_defs.items():
            for field, meta in fields.items():
                assert meta["example"].strip(), f"{table}.{field} has an empty example value"


# ═════════════════════════════════════════════════════════════════════════════
# Section 2: Integration tests — GET /analysis/field_coverage
# ═════════════════════════════════════════════════════════════════════════════

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

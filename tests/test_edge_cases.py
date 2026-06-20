"""Edge-case tests covering paths not exercised by the main suites.

Covers:
1. ``/analysis/*`` returning 503 when the database is empty.
2. Admin user-management edge cases (invalid role, 404 on unknown user, 401
   on missing auth, etc.).
3. The ``/auth/register/open`` and ``/admin/db-status`` endpoints.
4. ETL unit edge cases — rows missing required fields, ``first_order_date``
   getting rewound when an earlier order arrives, Tmall address parsing for
   malformed input.
"""
from __future__ import annotations

import asyncio
import pandas as pd
import pytest

from app.utils.cache import analysis_cache

# Reuse the API test fixtures (FastAPI TestClient + auth tokens).
from test_api_endpoints import client, tokens  # noqa: F401


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _reset_cache_between_tests():
    asyncio.run(analysis_cache.invalidate())
    yield
    asyncio.run(analysis_cache.invalidate())


# =============================================================================
# 1. /analysis/* returns 503 when the database is empty
# =============================================================================


class TestAnalysisEmptyDB:
    """All analysis endpoints must raise 503 before any data is uploaded."""

    PARAMS = {"start_date": "2025-07-01", "end_date": "2025-07-31"}

    def test_analyse_returns_503_when_empty(self, client, tokens):
        r = client.get(
            "/analysis/", params=self.PARAMS, headers=_auth(tokens["analyst"])
        )
        assert r.status_code == 503
        assert "Analysis data not initialised" in r.json()["detail"]

    def test_overview_returns_503_when_empty(self, client, tokens):
        r = client.get(
            "/analysis/overview", params=self.PARAMS, headers=_auth(tokens["analyst"])
        )
        assert r.status_code == 503

    def test_repurchase_rate_returns_503_when_empty(self, client, tokens):
        r = client.get(
            "/analysis/repurchase_rate",
            params=self.PARAMS,
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 503

    def test_customers_returns_503_when_empty(self, client, tokens):
        r = client.get(
            "/analysis/customers",
            params=self.PARAMS,
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 503

    def test_customer_orders_returns_503_when_empty(self, client, tokens):
        r = client.get(
            "/analysis/customers/13800138000",
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 503


# =============================================================================
# 2. Admin user-management edge cases
# =============================================================================


class TestAdminUserManagementEdges:
    def test_unauthenticated_request_rejected(self, client):
        """A request without a token must not reach the protected route."""
        r = client.get("/admin/users")
        assert r.status_code == 401

    def test_role_update_invalid_role_rejected(self, client, tokens):
        users = client.get("/admin/users", headers=_auth(tokens["admin"])).json()
        viewer_id = next(u["id"] for u in users if u["email"] == "viewer@test.com")

        r = client.put(
            f"/admin/users/{viewer_id}/role",
            json={"role": "superhero"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "Invalid role"

    def test_role_update_unknown_user_returns_404(self, client, tokens):
        # Well-formed UUID that does not exist
        bogus = "00000000-0000-0000-0000-000000000000"
        r = client.put(
            f"/admin/users/{bogus}/role",
            json={"role": "analyst"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 404

    def test_password_update_unknown_user_returns_404(self, client, tokens):
        bogus = "00000000-0000-0000-0000-000000000000"
        r = client.put(
            f"/admin/users/{bogus}/password",
            json={"password": "newpw"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 404

    def test_create_user_duplicate_email_rejected(self, client, tokens):
        r = client.post(
            "/admin/users",
            json={"email": "viewer@test.com", "password": "pw", "role": "viewer"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 400


# =============================================================================
# 3. /auth/register/open and /admin/db-status
# =============================================================================


class TestRegisterOpenEndpoint:
    def test_open_when_no_users(self, client):
        """Before the first user registers, the endpoint reports allowed=True."""
        r = client.get("/auth/register/open")
        assert r.status_code == 200
        assert r.json() == {"allowed": True}

    def test_closed_after_first_user_registers(self, client):
        # First registration auto-promotes to admin and closes registration.
        r_reg = client.post(
            "/auth/register",
            json={"email": "only@test.com", "password": "pw"},
        )
        assert r_reg.status_code == 201

        r = client.get("/auth/register/open")
        assert r.status_code == 200
        assert r.json() == {"allowed": False}


class TestDBStatusEndpoint:
    def test_db_status_requires_admin(self, client, tokens):
        r_viewer = client.get("/admin/db-status", headers=_auth(tokens["viewer"]))
        assert r_viewer.status_code == 403

        r_analyst = client.get("/admin/db-status", headers=_auth(tokens["analyst"]))
        assert r_analyst.status_code == 403

        r_admin = client.get("/admin/db-status", headers=_auth(tokens["admin"]))
        assert r_admin.status_code == 200

    def test_db_status_reports_empty_then_populated(self, client, tokens):
        r = client.get("/admin/db-status", headers=_auth(tokens["admin"]))
        assert r.status_code == 200
        body = r.json()
        assert body["analysis_ready"] is False
        assert body["all_orders_count"] == 0
        assert "user" in body["tables"]

        # Upload a row, then status should flip to ready.
        csv = (
            "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
            "1,2025-07-21,13800138000,item,1,10"
        )
        r_up = client.post(
            "/upload/", files={"file": ("x.csv", csv)}, headers=_auth(tokens["admin"])
        )
        assert r_up.status_code == 202

        r2 = client.get("/admin/db-status", headers=_auth(tokens["admin"]))
        body2 = r2.json()
        assert body2["analysis_ready"] is True
        assert body2["all_orders_count"] == 1


# =============================================================================
# 4. ETL unit edge cases (no DB needed)
# =============================================================================


class TestETLEdgeCases:
    """Unit-level edge cases in ``app.db.etl``."""

    def test_tmall_address_too_few_parts_returns_none_tuple(self):
        from app.db.etl import _parse_tmall_address

        # Only two comma-separated parts → cannot split out name/phone/address.
        assert _parse_tmall_address("张三，13800138000") == (None, None, None)

    def test_tmall_address_only_whitespace_returns_none_tuple(self):
        from app.db.etl import _parse_tmall_address

        assert _parse_tmall_address("   ") == (None, None, None)

    def test_ingest_skips_rows_with_missing_customer_key(self):
        """ingest() must skip rows whose ``客户标识`` resolves to ``None``."""
        from app.db.etl import ingest
        from unittest.mock import MagicMock
        from app.db.models import Customer, Order

        df = pd.DataFrame(
            [
                {
                    "订单号": "GOOD",
                    "买家付款时间": "2025-05-01",
                    "收货人手机号/提货人手机号": "13800001111",
                    "全部商品名称": "X",
                    "商品种类数": "1",
                    "订单实付金额": "99.00",
                },
                {
                    "订单号": "BAD-NO-CUSTOMER",
                    "买家付款时间": "2025-05-01",
                    "收货人手机号/提货人手机号": "",  # → customer_key None → skipped
                    "全部商品名称": "Y",
                    "商品种类数": "1",
                    "订单实付金额": "99.00",
                },
            ]
        )

        session = MagicMock()
        session.bind = None  # avoid the postgresql advisory-lock branch
        session.query.return_value.filter.return_value.all.return_value = []
        added: list = []
        session.add.side_effect = added.append

        inserted = ingest(df, session)
        assert inserted == 1

        order_adds = [o for o in added if isinstance(o, Order)]
        customer_adds = [c for c in added if isinstance(c, Customer)]
        assert len(order_adds) == 1
        assert order_adds[0].order_id == "GOOD"
        # Only one Customer should have been created — the bad row contributes none.
        assert len(customer_adds) == 1
        assert customer_adds[0].customer_key == "13800001111"

    def test_ingest_skips_rows_with_missing_order_date(self):
        """ingest() must skip rows whose date column cannot be parsed."""
        from app.db.etl import ingest
        from unittest.mock import MagicMock
        from app.db.models import Order

        df = pd.DataFrame(
            [
                {
                    "订单号": "BAD-NO-DATE",
                    "买家付款时间": "",  # → order_date None → skipped
                    "收货人手机号/提货人手机号": "13800001111",
                    "全部商品名称": "X",
                    "商品种类数": "1",
                    "订单实付金额": "99.00",
                },
            ]
        )

        session = MagicMock()
        session.bind = None
        session.query.return_value.filter.return_value.all.return_value = []
        added: list = []
        session.add.side_effect = added.append

        inserted = ingest(df, session)
        assert inserted == 0
        assert [o for o in added if isinstance(o, Order)] == []

    def test_ingest_rewinds_first_order_date_for_existing_customer(
        self, pg_sync_url
    ):
        """If a newer batch contains an earlier date, the existing customer's
        ``first_order_date`` must be rewound to the earlier value.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.etl import ingest
        from app.db.models import Customer

        engine = create_engine(pg_sync_url, future=True)
        SessionLocal = sessionmaker(bind=engine, future=True)
        try:
            with SessionLocal() as sess:
                later = pd.DataFrame(
                    [
                        {
                            "订单号": "L1",
                            "买家付款时间": "2025-06-15",
                            "收货人手机号/提货人手机号": "13800009999",
                            "全部商品名称": "X",
                            "商品种类数": "1",
                            "订单实付金额": "10",
                        }
                    ]
                )
                assert ingest(later, sess) == 1

                cust = sess.query(Customer).filter_by(
                    customer_key="13800009999"
                ).one()
                assert str(cust.first_order_date) == "2025-06-15"

                earlier = pd.DataFrame(
                    [
                        {
                            "订单号": "E1",
                            "买家付款时间": "2025-05-01",
                            "收货人手机号/提货人手机号": "13800009999",
                            "全部商品名称": "X",
                            "商品种类数": "1",
                            "订单实付金额": "10",
                        }
                    ]
                )
                assert ingest(earlier, sess) == 1

                sess.expire_all()
                cust = sess.query(Customer).filter_by(
                    customer_key="13800009999"
                ).one()
                assert str(cust.first_order_date) == "2025-05-01"
        finally:
            engine.dispose()
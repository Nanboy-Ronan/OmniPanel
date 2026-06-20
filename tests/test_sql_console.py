"""Tests for feature #3: Temporary SQL Query Console — POST /analysis/sql

Coverage plan
─────────────
Unit tests (no DB / no HTTP):
  - validate_sql_query() rejects non-SELECT statements
  - validate_sql_query() rejects multi-statement queries (semicolon separators)
  - validate_sql_query() rejects empty / blank SQL
  - validate_sql_query() accepts valid SELECT
  - enforce_limit() injects LIMIT when none present
  - enforce_limit() caps an existing LIMIT that exceeds 5000
  - enforce_limit() preserves a LIMIT that is within range

Integration tests (FastAPI TestClient + PostgreSQL):
  - viewer role → 403 Forbidden
  - analyst role with a valid SELECT → 200, returns rows + columns
  - admin role with a valid SELECT → 200
  - non-SELECT statement → 400
  - multi-statement SQL → 400
  - LIMIT exceeding 5000 → 400
  - empty sql body → 422 (Pydantic validation)
  - result capped at 5000 rows even without explicit LIMIT
  - operation_log entry is written on successful execution
  - statement_timeout is respected (query that would exceed 10 s returns error)
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ═════════════════════════════════════════════════════════════════════════════
# Section 1: Pure unit tests — SQL validation helpers
#
# These tests import and exercise the validation functions in isolation.
# They do NOT require a database connection or a running HTTP server.
# The functions are expected to live in app/views/analysis.py (or a separate
# app/utils/sql_validator.py if that is preferred).  Import paths are adjusted
# below once the implementation location is decided.
# ═════════════════════════════════════════════════════════════════════════════

# Lazy import so the module is only resolved when the tests actually run,
# letting us write the tests before the implementation exists.
def _import_validators():
    """Import the SQL validation helpers from wherever they end up living."""
    # Try the dedicated validator module first, fall back to analysis view.
    try:
        from app.utils import sql_validator as mod  # preferred location
        return mod.validate_sql_query, mod.enforce_limit
    except (ImportError, AttributeError):
        from app.views.ecommerce import analysis as mod  # fallback
        return mod.validate_sql_query, mod.enforce_limit


class TestValidateSqlQuery:
    """Unit tests for validate_sql_query(sql: str) -> None (raises on invalid)."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.validate, _ = _import_validators()

    # ── Happy path ──────────────────────────────────────────────────────────

    def test_simple_select_is_allowed(self):
        """A plain SELECT should not raise."""
        self.validate("SELECT id, name FROM orders LIMIT 10")

    def test_select_with_subquery(self):
        """SELECT with a subquery should be allowed."""
        self.validate("SELECT * FROM (SELECT id FROM orders) AS sub LIMIT 5")

    def test_select_case_insensitive(self):
        """Keyword matching should be case-insensitive."""
        self.validate("select * from orders limit 1")
        self.validate("Select * From orders Limit 1")

    def test_select_with_cte(self):
        """WITH ... SELECT (CTE) should be allowed."""
        self.validate("WITH cte AS (SELECT 1 AS n) SELECT n FROM cte")

    def test_select_with_whitespace_prefix(self):
        """Leading whitespace before SELECT is acceptable."""
        self.validate("   \n  SELECT 1")

    # ── Rejection: wrong statement type ────────────────────────────────────

    def test_insert_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("INSERT INTO orders (id) VALUES (1)")

    def test_update_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("UPDATE orders SET price = 0")

    def test_delete_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("DELETE FROM orders")

    def test_drop_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("DROP TABLE orders")

    def test_create_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("CREATE TABLE foo (id INT)")

    def test_truncate_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("TRUNCATE orders")

    def test_alter_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("ALTER TABLE orders ADD COLUMN foo TEXT")

    def test_call_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)select"):
            self.validate("CALL some_procedure()")

    # ── Rejection: multi-statement ──────────────────────────────────────────

    def test_semicolon_separator_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)multi|semicolon|single"):
            self.validate("SELECT 1; SELECT 2")

    def test_trailing_semicolon_is_rejected(self):
        """Even a single trailing semicolon should be rejected for safety."""
        with pytest.raises(ValueError, match="(?i)multi|semicolon|single"):
            self.validate("SELECT 1;")

    def test_comment_injection_attempt(self):
        """A comment-hidden second statement must be rejected."""
        with pytest.raises(ValueError):
            self.validate("SELECT 1; -- DROP TABLE orders")

    # ── Rejection: writable CTEs ────────────────────────────────────────────

    def test_writable_cte_update_is_rejected(self):
        """WITH ... (UPDATE ...) SELECT ... must be rejected (writable CTE)."""
        with pytest.raises(ValueError, match="(?i)forbidden|permitted|read.only"):
            self.validate(
                "WITH bad AS (UPDATE orders SET price = 0 RETURNING id) "
                "SELECT id FROM bad"
            )

    def test_writable_cte_insert_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)forbidden|permitted|read.only"):
            self.validate(
                "WITH bad AS (INSERT INTO orders (order_id, order_date, customer_key) "
                "VALUES ('x', '2025-01-01', 'k') RETURNING id) SELECT id FROM bad"
            )

    def test_writable_cte_delete_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)forbidden|permitted|read.only"):
            self.validate(
                "WITH bad AS (DELETE FROM orders WHERE id = 1 RETURNING id) "
                "SELECT id FROM bad"
            )

    def test_writable_cte_truncate_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)forbidden|permitted|read.only"):
            self.validate(
                "WITH bad AS (TRUNCATE orders) SELECT 1"
            )

    def test_read_only_cte_is_allowed(self):
        """A WITH that only reads data must pass all guards."""
        self.validate(
            "WITH latest AS "
            "(SELECT id, price FROM orders ORDER BY order_date DESC LIMIT 10) "
            "SELECT * FROM latest"
        )

    # ── Rejection: SELECT INTO ──────────────────────────────────────────────

    def test_select_into_is_rejected(self):
        """SELECT ... INTO tablename writes a new PostgreSQL table — must be blocked."""
        with pytest.raises(ValueError, match="(?i)into|permitted|allow"):
            self.validate("SELECT id INTO backup_orders FROM orders")

    def test_select_into_multiline_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)into|permitted|allow"):
            self.validate(
                "SELECT id,\n       price\nINTO backup_orders\nFROM orders\nLIMIT 100"
            )

    # ── No false positives on string literals ───────────────────────────────

    def test_string_literal_update_keyword_is_allowed(self):
        """DML keyword inside a quoted string must NOT trigger rejection."""
        self.validate(
            "SELECT * FROM operation_log WHERE action = 'update_role' LIMIT 10"
        )

    def test_string_literal_delete_keyword_is_allowed(self):
        self.validate(
            "SELECT * FROM operation_log WHERE action = 'delete_user' LIMIT 10"
        )

    def test_string_literal_insert_keyword_is_allowed(self):
        self.validate(
            "SELECT id FROM orders WHERE sku = 'insert_test_sku' LIMIT 5"
        )

    # ── Rejection: empty / blank ────────────────────────────────────────────

    def test_empty_string_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)empty|blank|sql"):
            self.validate("")

    def test_whitespace_only_is_rejected(self):
        with pytest.raises(ValueError, match="(?i)empty|blank|sql"):
            self.validate("   \n\t  ")


class TestEnforceLimit:
    """Unit tests for enforce_limit(sql: str, max_limit: int = 5000) -> str."""

    @pytest.fixture(autouse=True)
    def _load(self):
        _, self.enforce = _import_validators()

    def test_no_limit_gets_injected(self):
        result = self.enforce("SELECT * FROM orders")
        assert "limit" in result.lower()
        # The injected limit must be <= 5000
        import re
        m = re.search(r"limit\s+(\d+)", result, re.IGNORECASE)
        assert m is not None
        assert int(m.group(1)) <= 5000

    def test_limit_within_range_is_preserved(self):
        result = self.enforce("SELECT * FROM orders LIMIT 100")
        import re
        m = re.search(r"limit\s+(\d+)", result, re.IGNORECASE)
        assert m is not None
        assert int(m.group(1)) == 100

    def test_limit_at_boundary_is_preserved(self):
        result = self.enforce("SELECT * FROM orders LIMIT 5000")
        import re
        m = re.search(r"limit\s+(\d+)", result, re.IGNORECASE)
        assert m is not None
        assert int(m.group(1)) == 5000

    def test_limit_exceeding_max_is_rejected_or_capped(self):
        """LIMIT > 5000 must either raise ValueError or be capped at 5000."""
        import re
        try:
            result = self.enforce("SELECT * FROM orders LIMIT 9999")
            m = re.search(r"limit\s+(\d+)", result, re.IGNORECASE)
            assert m is not None and int(m.group(1)) <= 5000
        except ValueError:
            pass  # also acceptable — explicit rejection

    def test_custom_max_limit(self):
        result = self.enforce("SELECT * FROM orders", max_limit=50)
        import re
        m = re.search(r"limit\s+(\d+)", result, re.IGNORECASE)
        assert m is not None
        assert int(m.group(1)) <= 50


# ═════════════════════════════════════════════════════════════════════════════
# Section 2: Integration tests — POST /analysis/sql API endpoint
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client(pg_async_url, monkeypatch):
    """Reusable FastAPI TestClient wired to the isolated test database."""
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    import app.db as db

    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", SessionLocal, raising=False)

    import app.main
    importlib.reload(app.main)

    from fastapi.testclient import TestClient
    with TestClient(app.main.app) as c:
        yield c

    import asyncio
    asyncio.run(engine.dispose())


@pytest.fixture
def tokens(client):
    """Create viewer / analyst / admin users and return their JWT tokens."""
    # First registration → auto-promoted to admin
    client.post("/auth/register", json={"email": "first@test.com", "password": "pw", "role": "viewer"})
    r_login = client.post("/auth/jwt/login", data={"username": "first@test.com", "password": "pw"})
    admin_token = r_login.json()["access_token"]

    def _create(email, role):
        r = client.post(
            "/admin/users",
            json={"email": email, "password": "pw", "role": role},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 201, r.text
        r_l = client.post("/auth/jwt/login", data={"username": email, "password": "pw"})
        return r_l.json()["access_token"]

    return {role: _create(f"{role}@test.com", role) for role in ["viewer", "analyst", "admin"]}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Access-control tests ──────────────────────────────────────────────────────

class TestSqlConsolePermissions:
    def test_unauthenticated_returns_401(self, client):
        r = client.post("/analysis/sql", json={"sql": "SELECT 1"})
        assert r.status_code in (401, 403)

    def test_viewer_is_forbidden(self, client, tokens):
        r = client.post("/analysis/sql", json={"sql": "SELECT 1"}, headers=_auth(tokens["viewer"]))
        assert r.status_code == 403

    def test_analyst_can_access(self, client, tokens):
        r = client.post("/analysis/sql", json={"sql": "SELECT 1"}, headers=_auth(tokens["analyst"]))
        # 200 or 503 (no data) — both indicate the auth check passed
        assert r.status_code in (200, 503)

    def test_admin_can_access(self, client, tokens):
        r = client.post("/analysis/sql", json={"sql": "SELECT 1"}, headers=_auth(tokens["admin"]))
        assert r.status_code in (200, 503)


# ── Request validation tests ──────────────────────────────────────────────────

class TestSqlConsoleRequestValidation:
    def test_missing_sql_field_returns_422(self, client, tokens):
        """Body without 'sql' key should fail Pydantic validation."""
        r = client.post("/analysis/sql", json={}, headers=_auth(tokens["analyst"]))
        assert r.status_code == 422

    def test_empty_sql_returns_400(self, client, tokens):
        r = client.post("/analysis/sql", json={"sql": ""}, headers=_auth(tokens["analyst"]))
        assert r.status_code == 400

    def test_whitespace_sql_returns_400(self, client, tokens):
        r = client.post("/analysis/sql", json={"sql": "   "}, headers=_auth(tokens["analyst"]))
        assert r.status_code == 400

    def test_insert_returns_400(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "INSERT INTO orders (id) VALUES (gen_random_uuid())"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400
        assert "select" in r.json()["detail"].lower()

    def test_update_returns_400(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "UPDATE orders SET price = 0"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_delete_returns_400(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "DELETE FROM orders"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_drop_returns_400(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "DROP TABLE orders"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_multi_statement_returns_400(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT 1; SELECT 2"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_trailing_semicolon_returns_400(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT 1;"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_limit_exceeding_5000_returns_400(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM orders LIMIT 9999"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400
        assert "limit" in r.json()["detail"].lower()


# ── Successful query tests ────────────────────────────────────────────────────

class TestSqlConsoleSuccess:
    @pytest.fixture(autouse=True)
    def _seed_data(self, client, tokens):
        """Upload a minimal order row so SELECT queries return real data."""
        csv_data = (
            "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
            "1001,2025-07-01,13800001111,Test SKU,1,99.00\n"
            "1002,2025-07-02,13800002222,Test SKU B,1,199.00\n"
        )
        r = client.post(
            "/upload/",
            files={"file": ("seed.csv", csv_data)},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 202, r.text

    def test_valid_select_returns_200_with_rows_and_columns(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT id FROM orders LIMIT 10"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert "rows" in body
        assert "columns" in body
        assert isinstance(body["rows"], list)
        assert isinstance(body["columns"], list)

    def test_row_count_is_returned(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM orders LIMIT 10"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert "row_count" in body
        assert body["row_count"] == len(body["rows"])

    def test_column_names_match_query(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT platform, price FROM orders LIMIT 5"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200
        cols = r.json()["columns"]
        assert "platform" in cols
        assert "price" in cols

    def test_limit_5000_boundary_is_accepted(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM orders LIMIT 5000"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200

    def test_select_without_limit_auto_applies_cap(self, client, tokens):
        """If the user omits LIMIT, the backend should inject one (≤ 5000)."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM orders"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["row_count"] <= 5000

    def test_case_insensitive_select_keyword(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "select * from orders limit 1"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200

    def test_select_constant_expression(self, client, tokens):
        """SELECT without a table (e.g. SELECT 1) should work."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT 1 AS n"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rows"][0][0] == 1 or body["rows"][0]["n"] == 1  # list or dict rows

    def test_admin_can_run_query(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT COUNT(*) AS cnt FROM orders"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 200


# ── Operation-log tests ───────────────────────────────────────────────────────

class TestSqlConsoleOperationLog:
    @pytest.fixture(autouse=True)
    def _seed_data(self, client, tokens):
        csv_data = (
            "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
            "9001,2025-07-10,13800009999,Log SKU,1,55.50\n"
        )
        r = client.post(
            "/upload/",
            files={"file": ("log_seed.csv", csv_data)},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 202, r.text

    def test_successful_query_creates_operation_log(self, client, tokens):
        client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM orders LIMIT 1"},
            headers=_auth(tokens["analyst"]),
        )
        r_logs = client.get("/admin/logs", headers=_auth(tokens["admin"]))
        assert r_logs.status_code == 200
        sql_logs = [l for l in r_logs.json() if l.get("action") == "sql_query"]
        assert len(sql_logs) >= 1

    def test_log_detail_contains_sql(self, client, tokens):
        sql = "SELECT id FROM orders LIMIT 1"
        client.post(
            "/analysis/sql",
            json={"sql": sql},
            headers=_auth(tokens["analyst"]),
        )
        r_logs = client.get("/admin/logs", headers=_auth(tokens["admin"]))
        sql_logs = [l for l in r_logs.json() if l.get("action") == "sql_query"]
        assert any(sql in (l.get("detail") or {}).get("sql", "") for l in sql_logs)

    def test_log_detail_contains_row_count(self, client, tokens):
        client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM orders LIMIT 5"},
            headers=_auth(tokens["analyst"]),
        )
        r_logs = client.get("/admin/logs", headers=_auth(tokens["admin"]))
        sql_logs = [l for l in r_logs.json() if l.get("action") == "sql_query"]
        assert all("row_count" in (l.get("detail") or {}) for l in sql_logs)

    def test_rejected_query_does_not_create_log(self, client, tokens):
        """Bad SQL (non-SELECT) must not produce an operation_log entry."""
        # Record baseline count
        r_before = client.get("/admin/logs", headers=_auth(tokens["admin"]))
        before_count = len([l for l in r_before.json() if l.get("action") == "sql_query"])

        client.post(
            "/analysis/sql",
            json={"sql": "DROP TABLE orders"},
            headers=_auth(tokens["analyst"]),
        )

        r_after = client.get("/admin/logs", headers=_auth(tokens["admin"]))
        after_count = len([l for l in r_after.json() if l.get("action") == "sql_query"])
        assert after_count == before_count


# ── Edge / security tests ─────────────────────────────────────────────────────

class TestSqlConsoleEdgeCases:
    def test_comment_injection_blocked(self, client, tokens):
        """SQL injection via comments embedding a second statement."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT 1; -- DROP TABLE orders"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    # ── Writable CTE attacks ──────────────────────────────────────────────────

    def test_writable_cte_update_blocked(self, client, tokens):
        """Writable CTE with UPDATE must be rejected before reaching the database."""
        r = client.post(
            "/analysis/sql",
            json={
                "sql": (
                    "WITH bad AS "
                    "(UPDATE orders SET price = 0 RETURNING id) "
                    "SELECT id FROM bad"
                )
            },
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "forbidden" in detail or "permitted" in detail

    def test_writable_cte_delete_blocked(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={
                "sql": (
                    "WITH bad AS "
                    "(DELETE FROM orders RETURNING id) "
                    "SELECT id FROM bad"
                )
            },
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_writable_cte_insert_blocked(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={
                "sql": (
                    "WITH bad AS "
                    "(INSERT INTO orders (order_id, order_date, customer_key) "
                    "VALUES ('x', '2025-01-01', 'k') RETURNING id) "
                    "SELECT id FROM bad"
                )
            },
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_select_into_blocked(self, client, tokens):
        """SELECT ... INTO must be rejected — it creates a new table."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT * INTO backup_orders FROM orders LIMIT 10"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "into" in detail or "permitted" in detail

    def test_string_literal_dml_keyword_passes(self, client, tokens):
        """Filtering by a value that contains a DML keyword must not be blocked."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM operation_log WHERE action = 'update_role' LIMIT 5"},
            headers=_auth(tokens["analyst"]),
        )
        # 200 (rows found) or 503 (no data yet) — both mean validation passed
        assert r.status_code in (200, 503)

    def test_union_select_is_allowed(self, client, tokens):
        """UNION between two SELECTs is a valid read-only query."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT 1 AS n UNION SELECT 2 AS n"},
            headers=_auth(tokens["analyst"]),
        )
        # 200 is the expected happy path; 400 would be a bug
        assert r.status_code == 200

    def test_cte_select_is_allowed(self, client, tokens):
        r = client.post(
            "/analysis/sql",
            json={"sql": "WITH t AS (SELECT 1 AS n) SELECT n FROM t"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200

    def test_very_long_sql_handled_gracefully(self, client, tokens):
        """Extremely long (but valid) SQL should not crash the server."""
        # Build a SELECT with many columns aliased
        cols = ", ".join(f"1 AS col_{i}" for i in range(200))
        r = client.post(
            "/analysis/sql",
            json={"sql": f"SELECT {cols}"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code in (200, 400)  # 400 only if backend imposes length limit

    def test_select_with_limit_0_returns_empty(self, client, tokens):
        """LIMIT 0 is a valid SELECT; result should be empty rows list."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT * FROM orders LIMIT 0"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200
        assert r.json()["rows"] == []
        assert r.json()["row_count"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Section 5: DB-level read-only enforcement
#
# The endpoint sets SET LOCAL transaction_read_only = on BEFORE executing
# the user's SQL. This ensures that even if the application-level validator
# is somehow bypassed, PostgreSQL itself will reject any DML statement.
# ═════════════════════════════════════════════════════════════════════════════

class TestSqlReadOnlyDbProtection:

    def test_db_rejects_update_even_if_validator_bypassed(self, client, tokens, monkeypatch):
        """DB-level read-only mode must block DML independently of the app validator."""
        from app.views.ecommerce import analysis as analysis_mod
        # Bypass both application-level guards
        monkeypatch.setattr(analysis_mod, "validate_sql_query", lambda sql: None)
        monkeypatch.setattr(analysis_mod, "enforce_limit", lambda sql, **kw: sql)

        r = client.post(
            "/analysis/sql",
            json={"sql": "UPDATE orders SET price = 0"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "read-only" in detail or "cannot execute" in detail

    def test_db_rejects_delete_even_if_validator_bypassed(self, client, tokens, monkeypatch):
        from app.views.ecommerce import analysis as analysis_mod
        monkeypatch.setattr(analysis_mod, "validate_sql_query", lambda sql: None)
        monkeypatch.setattr(analysis_mod, "enforce_limit", lambda sql, **kw: sql)

        r = client.post(
            "/analysis/sql",
            json={"sql": "DELETE FROM orders"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "read-only" in detail or "cannot execute" in detail

    def test_db_rejects_drop_even_if_validator_bypassed(self, client, tokens, monkeypatch):
        from app.views.ecommerce import analysis as analysis_mod
        monkeypatch.setattr(analysis_mod, "validate_sql_query", lambda sql: None)
        monkeypatch.setattr(analysis_mod, "enforce_limit", lambda sql, **kw: sql)

        r = client.post(
            "/analysis/sql",
            json={"sql": "DROP TABLE orders"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_read_only_does_not_block_valid_select(self, client, tokens):
        """The read-only setting must not interfere with legitimate SELECT queries."""
        r = client.post(
            "/analysis/sql",
            json={"sql": "SELECT 1 AS n"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200

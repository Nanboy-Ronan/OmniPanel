"""Shared fixtures for all test modules.

Creates an isolated test database for each test session and truncates tables
between individual tests. Uses psycopg2 to connect directly to PostgreSQL so
no Docker CLI is required.

Database tests may use the same PostgreSQL server as a deployed app, but they
must never use the deployed application database. The configured URL is used
only to locate the server and credentials; tests connect to an admin database,
create a fresh ``rpa_test_*`` database, run there, and drop it at teardown.
"""

import os
import re
import sys
import uuid
from pathlib import Path

import psycopg2
import psycopg2.extensions
from psycopg2 import sql
import pytest
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("RAP_CREATE_TABLES_ON_STARTUP", "false")
os.environ.setdefault("RAP_DISABLE_MONTHLY_BACKUP", "true")
os.environ.setdefault("RAP_TEST_FAST_PASSWORDS", "true")
os.environ.setdefault("RAP_SECRET", "test-secret-do-not-use-in-production")

ADMIN_DATABASE = "postgres"

# ── Fast test password helper (replaces bcrypt for speed in tests) ───────────

class _PlainTestPasswordHelper:
    """Deterministic password helper that avoids bcrypt cost in tests."""

    def hash(self, password: str) -> str:
        return f"test${password}"

    def verify_and_update(self, plain_password: str, hashed_password: str):
        return hashed_password == self.hash(plain_password), None


if os.getenv("RAP_TEST_FAST_PASSWORDS", "false").lower() in ("1", "true", "yes"):
    import app.auth as _auth_module  # noqa: E402
    _auth_module._password_helper = lambda: _PlainTestPasswordHelper()


def _normalize_pg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg", "postgresql").replace("+asyncpg", "")


def _configured_pg_url() -> str:
    """Return the configured PostgreSQL URL used only for host/user/password."""
    explicit = os.getenv("PG_TEST_URL")
    if explicit:
        return _normalize_pg_url(explicit)
    rap_url = os.getenv("RAP_DATABASE_URL", "")
    if rap_url:
        return _normalize_pg_url(rap_url)
    return "postgresql://rpa:rpa@127.0.0.1:5432/rpa"


def _replace_database(url: str, db_name: str) -> str:
    if not db_name:
        raise RuntimeError("Database name cannot be empty")
    return re.sub(r"/[^/]*$", f"/{db_name}", url)


def _admin_pg_url() -> str:
    """Return a safe admin URL; never return the configured application DB."""
    return _replace_database(_configured_pg_url(), ADMIN_DATABASE)


def _db_url(db_name: str) -> str:
    """Swap the database name in the base URL."""
    if not db_name.startswith("rpa_test_") and db_name != ADMIN_DATABASE:
        raise RuntimeError(f"Refusing to build non-test database URL for {db_name!r}")
    return _replace_database(_configured_pg_url(), db_name)


def _async_db_url(db_name: str) -> str:
    """Return an asyncpg SQLAlchemy URL for the named test database."""
    return _db_url(db_name).replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="session")
def _pg_db():
    """Create a unique isolated test database for the session."""
    test_db = f"rpa_test_{uuid.uuid4().hex[:12]}"
    admin_url = _admin_pg_url()

    # CREATE DATABASE must run outside a transaction
    try:
        conn = psycopg2.connect(admin_url)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PostgreSQL test database is not reachable: {exc}")

    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db)))
    conn.close()

    from app.db import Base
    import app.db.models  # noqa: F401

    schema_engine = create_engine(_db_url(test_db), future=True)
    Base.metadata.create_all(schema_engine)
    schema_engine.dispose()

    yield test_db

    # DROP DATABASE (also outside a transaction)
    conn = psycopg2.connect(admin_url)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        if not test_db.startswith("rpa_test_"):
            raise RuntimeError(f"Refusing to drop non-test database {test_db!r}")
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(test_db)))
    conn.close()


@pytest.fixture(autouse=True)
def _noop_load_dotenv(monkeypatch):
    """Prevent app/main.py's module-level load_dotenv() from re-injecting real
    .env values on each importlib.reload(app.main) call in test fixtures.

    Without this, env vars set via monkeypatch.delenv() or os.environ.setdefault()
    are silently overwritten by the real .env on each reload, causing isolation
    failures in any test that calls reload() and then inspects Settings() values
    (e.g. WECOM_DEFAULT_ROLE, WECOM_STREAMLIT_REDIRECT_URI).

    The real .env has already been loaded into os.environ before pytest collects
    tests, so making reload-time calls a no-op removes nothing needed.
    """
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)


@pytest.fixture(autouse=True)
def _clean_db(request):
    """Truncate all data tables between tests so each test starts fresh."""
    db_fixtures = {"_pg_db", "pg_sync_url", "pg_async_url", "client", "api_client", "db_session"}
    if not db_fixtures.intersection(request.fixturenames):
        return

    pg_db = request.getfixturevalue("_pg_db")
    if not pg_db.startswith("rpa_test_"):
        raise RuntimeError(f"Refusing to clean non-test database {pg_db!r}")
    data_tables = [
        "media_post_metrics_daily",
        "media_posts",
        "media_sync_runs",
        "media_accounts",
        "collector_runs",
        "upload_rejected_rows",
        "youzan_orders",
        "jd_orders",
        "tmall_orders",
        "upload_batches",
        "operation_log",
        "orders",
        "customers",
        "user",
    ]
    conn = psycopg2.connect(_db_url(pg_db))
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename = ANY(%s)
                """,
                (data_tables,),
            )
            existing = {row[0] for row in cur.fetchall()}
            ordered = [name for name in data_tables if name in existing]
            if ordered:
                identifiers = sql.SQL(", ").join(sql.Identifier(name) for name in ordered)
                cur.execute(sql.SQL("TRUNCATE {} CASCADE").format(identifiers))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    # analysis_cache is a module-level singleton, not reset by the TRUNCATE
    # above. Endpoints whose cache key carries no params (e.g.
    # /analysis/latest_order_date) would otherwise leak a stale value from
    # whichever earlier test in this session populated that key.
    import asyncio
    from app.utils.cache import analysis_cache
    asyncio.run(analysis_cache.invalidate())


@pytest.fixture
def pg_sync_url(_pg_db):
    return _db_url(_pg_db)


@pytest.fixture
def pg_async_url(_pg_db):
    return _async_db_url(_pg_db)


# ── Shared upload helper ──────────────────────────────────────────────────────

def upload_and_poll(client, auth_headers, file_bytes, filename="data.csv"):
    """Upload a file and return the completed batch dict.

    POST /upload/ now returns 202 with a batch_id. This helper waits for the
    batch to reach a terminal state and returns its data. With TestClient,
    BackgroundTasks run synchronously so the batch is immediately queryable.

    The returned dict includes an ``inserted_rows`` alias for ``inserted_orders``
    so existing assertions that use either key continue to work.
    """
    r = client.post(
        "/upload/",
        files={"file": (filename, file_bytes)},
        headers=auth_headers,
    )
    assert r.status_code == 202, f"Expected 202 but got {r.status_code}: {r.text}"
    batch_id = r.json()["batch_id"]
    r2 = client.get(f"/upload/batches/{batch_id}", headers=auth_headers)
    assert r2.status_code == 200, f"Batch status endpoint returned {r2.status_code}: {r2.text}"
    batch = r2.json()
    batch.setdefault("inserted_rows", batch.get("inserted_orders", 0))
    return batch

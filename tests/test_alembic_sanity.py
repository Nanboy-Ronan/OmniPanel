"""Alembic migration sanity tests.

TDD: written before the Makefile / workflow is finalised.

Covers:
1. Metadata completeness — all expected tables are registered in Base.metadata
   (catches missing imports or dynamic-model factory failures). No DB needed.
2. alembic upgrade head on a fresh (empty) database creates all expected tables.
3. Running upgrade head twice is idempotent (no error on second run).
4. Downgrade to base then re-upgrade restores all tables.
5. alembic check exits 0 after a successful upgrade (no unapplied migrations).

The fresh-DB tests spin up a temporary PostgreSQL database (rpa_test_alembic_*),
run alembic via subprocess (so the real CLI code path is exercised), then drop the
database in teardown.  They are skipped if PostgreSQL is unreachable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
import psycopg2.extensions
import pytest
from psycopg2 import sql

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ── helpers ───────────────────────────────────────────────────────────────────

def _admin_pg_url() -> str:
    base = os.getenv("PG_TEST_URL") or os.getenv("RAP_DATABASE_URL") or \
        "postgresql://rpa:rpa@127.0.0.1:5432/rpa"
    base = base.replace("postgresql+asyncpg", "postgresql").replace("+asyncpg", "")
    import re
    return re.sub(r"/[^/]*$", "/postgres", base)


def _make_async_url(sync_url: str) -> str:
    if "postgresql+asyncpg" in sync_url:
        return sync_url
    return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fresh_pg_db():
    """Create an empty test database, yield its async URL, drop it at teardown."""
    db_name = f"rpa_test_alembic_{uuid.uuid4().hex[:10]}"
    admin_url = _admin_pg_url()

    try:
        conn = psycopg2.connect(admin_url)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")

    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    conn.close()

    import re
    sync_url = re.sub(r"/[^/]*$", f"/{db_name}", admin_url)
    async_url = _make_async_url(sync_url)

    yield async_url, sync_url, db_name

    conn2 = psycopg2.connect(admin_url)
    conn2.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn2.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
    conn2.close()


def _alembic(args: list[str], async_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "RAP_DATABASE_URL": async_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic"] + args,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )


def _table_names(sync_url: str) -> set[str]:
    conn = psycopg2.connect(sync_url)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        result = {row[0] for row in cur.fetchall()}
    conn.close()
    return result


# ── expected tables ───────────────────────────────────────────────────────────

_CORE_TABLES = {
    "user",
    "customers",
    "orders",
    "upload_batches",
    "upload_rejected_rows",
    "operation_log",
    "media_accounts",
    "media_posts",
    "media_post_metrics_daily",
    "media_sync_runs",
}

_PLATFORM_RAW_TABLES = {"youzan_orders", "jd_orders", "tmall_orders"}

_ALL_APP_TABLES = _CORE_TABLES | _PLATFORM_RAW_TABLES


# ── 1. Metadata completeness (no DB required) ─────────────────────────────────

class TestMetadataCompleteness:
    """All ORM models must be importable and registered with Base.metadata."""

    def setup_method(self):
        import app.db.models  # noqa: F401 — ensure dynamic models are created
        from app.db import Base
        self.tables = set(Base.metadata.tables.keys())

    def test_core_tables_registered(self):
        for tbl in _CORE_TABLES:
            assert tbl in self.tables, f"Table {tbl!r} missing from Base.metadata"

    def test_platform_raw_tables_registered(self):
        for tbl in _PLATFORM_RAW_TABLES:
            assert tbl in self.tables, f"Table {tbl!r} missing from Base.metadata"

    def test_alembic_version_not_in_orm_metadata(self):
        """alembic_version is Alembic's own table, not part of the ORM."""
        assert "alembic_version" not in self.tables


# ── 2. alembic upgrade / downgrade (requires live PostgreSQL) ─────────────────

class TestAlembicUpgrade:
    def test_upgrade_head_creates_all_tables(self, fresh_pg_db):
        async_url, sync_url, _ = fresh_pg_db
        result = _alembic(["upgrade", "head"], async_url)
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )
        tables = _table_names(sync_url)
        for tbl in _ALL_APP_TABLES:
            assert tbl in tables, f"Table {tbl!r} missing after upgrade head"

    def test_upgrade_head_creates_alembic_version_table(self, fresh_pg_db):
        async_url, sync_url, _ = fresh_pg_db
        _alembic(["upgrade", "head"], async_url)
        assert "alembic_version" in _table_names(sync_url)

    def test_upgrade_head_is_idempotent(self, fresh_pg_db):
        async_url, sync_url, _ = fresh_pg_db
        for run in range(2):
            result = _alembic(["upgrade", "head"], async_url)
            assert result.returncode == 0, (
                f"alembic upgrade head failed on run {run + 1}:\n{result.stderr}"
            )

    def test_downgrade_base_then_upgrade_head(self, fresh_pg_db):
        async_url, sync_url, _ = fresh_pg_db
        # Start from head
        r1 = _alembic(["upgrade", "head"], async_url)
        assert r1.returncode == 0, r1.stderr

        # Downgrade back to base (no migrations applied)
        r2 = _alembic(["downgrade", "base"], async_url)
        assert r2.returncode == 0, r2.stderr

        # Re-upgrade
        r3 = _alembic(["upgrade", "head"], async_url)
        assert r3.returncode == 0, r3.stderr

        tables = _table_names(sync_url)
        for tbl in _ALL_APP_TABLES:
            assert tbl in tables, f"Table {tbl!r} missing after downgrade+upgrade"

    def test_alembic_check_passes_after_upgrade(self, fresh_pg_db):
        """`alembic check` exits 0 when database is at head revision."""
        async_url, sync_url, _ = fresh_pg_db
        _alembic(["upgrade", "head"], async_url)
        result = _alembic(["check"], async_url)
        assert result.returncode == 0, (
            f"alembic check reported pending migrations:\n{result.stdout}\n{result.stderr}"
        )

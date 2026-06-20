from __future__ import annotations

import pytest

import conftest


def test_configured_pg_url_prefers_explicit_test_url(monkeypatch):
    monkeypatch.setenv("PG_TEST_URL", "postgresql+asyncpg://rpa:rpa@127.0.0.1:55432/rpa")
    monkeypatch.setenv("RAP_DATABASE_URL", "postgresql+asyncpg://rpa:rpa@prod:5432/rpa")

    assert conftest._configured_pg_url() == "postgresql://rpa:rpa@127.0.0.1:55432/rpa"


def test_configured_pg_url_can_read_application_url(monkeypatch):
    monkeypatch.delenv("PG_TEST_URL", raising=False)
    monkeypatch.setenv("RAP_DATABASE_URL", "postgresql+asyncpg://rpa:rpa@prod:5432/rpa")

    assert conftest._configured_pg_url() == "postgresql://rpa:rpa@prod:5432/rpa"


def test_admin_url_never_uses_application_database(monkeypatch):
    monkeypatch.delenv("PG_TEST_URL", raising=False)
    monkeypatch.setenv("RAP_DATABASE_URL", "postgresql+asyncpg://rpa:rpa@prod:5432/rpa")

    assert conftest._admin_pg_url() == "postgresql://rpa:rpa@prod:5432/postgres"


def test_admin_url_rewrites_explicit_test_url_database(monkeypatch):
    monkeypatch.setenv("PG_TEST_URL", "postgresql://rpa:rpa@127.0.0.1:55432/rpa")

    assert conftest._admin_pg_url() == "postgresql://rpa:rpa@127.0.0.1:55432/postgres"


def test_db_url_refuses_non_test_database(monkeypatch):
    monkeypatch.setenv("PG_TEST_URL", "postgresql://rpa:rpa@127.0.0.1:55432/postgres")

    with pytest.raises(RuntimeError):
        conftest._db_url("rpa")


def test_db_url_allows_generated_test_database(monkeypatch):
    monkeypatch.setenv("PG_TEST_URL", "postgresql://rpa:rpa@127.0.0.1:55432/postgres")

    assert conftest._db_url("rpa_test_abc123").endswith("/rpa_test_abc123")


def test_db_url_uses_same_server_but_test_database(monkeypatch):
    monkeypatch.delenv("PG_TEST_URL", raising=False)
    monkeypatch.setenv("RAP_DATABASE_URL", "postgresql+asyncpg://rpa:rpa@prod:5432/rpa")

    assert conftest._db_url("rpa_test_abc123") == (
        "postgresql://rpa:rpa@prod:5432/rpa_test_abc123"
    )

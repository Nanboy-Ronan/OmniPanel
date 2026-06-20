"""Tests for 中文问数据 (NL-to-SQL) — POST /analysis/nl-sql.

The model call (``app.utils.nl_to_sql.generate_sql``) is monkeypatched in every
test, so these run with no Anthropic API key and no network. They verify that:

  - the endpoint reuses the SQL-console safety pipeline (validate + read-only),
  - the generated SQL is always returned to the client (transparency),
  - unsafe / unexecutable generated SQL surfaces as an ``error`` field, not a crash,
  - access control matches the SQL console (analyst+),
  - the feature returns 503 when no API key is configured.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── fixtures (mirror tests/test_sql_console.py) ───────────────────────────────
@pytest.fixture
def client(pg_async_url, monkeypatch):
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
    client.post("/auth/register", json={"email": "first@test.com", "password": "pw", "role": "viewer"})
    admin_token = client.post(
        "/auth/jwt/login", data={"username": "first@test.com", "password": "pw"}
    ).json()["access_token"]

    def _create(email, role):
        r = client.post(
            "/admin/users",
            json={"email": email, "password": "pw", "role": role},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 201, r.text
        return client.post(
            "/auth/jwt/login", data={"username": email, "password": "pw"}
        ).json()["access_token"]

    return {role: _create(f"{role}@test.com", role) for role in ["viewer", "analyst", "admin"]}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _patch_generate(monkeypatch, sql: str, explanation: str = "测试说明"):
    """Replace the model call with a deterministic async stub."""
    import app.utils.nl_to_sql as nl

    async def _fake(question: str, provider: str | None = None, model: str | None = None):
        return sql, explanation

    monkeypatch.setattr(nl, "generate_sql", _fake)


# ── access control ────────────────────────────────────────────────────────────
def test_unauthenticated_returns_401_or_403(client):
    r = client.post("/analysis/nl-sql", json={"question": "多少订单"})
    assert r.status_code in (401, 403)


def test_viewer_is_forbidden(client, tokens, monkeypatch):
    _patch_generate(monkeypatch, "SELECT 1")
    r = client.post("/analysis/nl-sql", json={"question": "多少订单"}, headers=_auth(tokens["viewer"]))
    assert r.status_code == 403


def test_empty_question_returns_400(client, tokens, monkeypatch):
    _patch_generate(monkeypatch, "SELECT 1")
    r = client.post("/analysis/nl-sql", json={"question": "   "}, headers=_auth(tokens["analyst"]))
    assert r.status_code == 400


# ── happy path ────────────────────────────────────────────────────────────────
def test_generated_select_executes_and_returns_rows(client, tokens, monkeypatch):
    # A constant SELECT always returns a row, so this verifies the full
    # generate → validate → execute pipeline without depending on seeded data
    # (background ingestion is async and racy in tests).
    _patch_generate(monkeypatch, "SELECT 1 AS n")
    r = client.post(
        "/analysis/nl-sql",
        json={"question": "随便查一行"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert "n" in body["columns"]
    assert body["row_count"] == len(body["rows"]) == 1
    # The generated SQL is returned (transparency), with an auto-injected LIMIT.
    assert "SELECT 1 AS n" in body["sql"]
    assert "limit" in body["sql"].lower()
    assert body["explanation"] == "测试说明"


def test_successful_query_writes_nl_sql_log(client, tokens, monkeypatch):
    _patch_generate(monkeypatch, "SELECT 1 AS n")
    client.post(
        "/analysis/nl-sql",
        json={"question": "总订单数"},
        headers=_auth(tokens["analyst"]),
    )
    logs = client.get("/admin/logs", headers=_auth(tokens["admin"])).json()
    nl_logs = [l for l in logs if l.get("action") == "nl_sql_query"]
    assert len(nl_logs) >= 1
    assert "question" in (nl_logs[0].get("detail") or {})


# ── safety: untrusted model output still goes through the guards ───────────────
def test_unsafe_generated_sql_is_blocked_with_error_field(client, tokens, monkeypatch):
    _patch_generate(monkeypatch, "DROP TABLE orders")
    r = client.post(
        "/analysis/nl-sql",
        json={"question": "删库"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is not None
    assert "安全校验" in body["error"]
    assert body["rows"] == []


def test_empty_generated_sql_returns_error_field(client, tokens, monkeypatch):
    _patch_generate(monkeypatch, "", explanation="无法用现有表回答这个问题。")
    r = client.post(
        "/analysis/nl-sql",
        json={"question": "今天天气如何"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "无法用现有表回答这个问题。"
    assert body["sql"] == ""


def test_execution_error_surfaces_in_error_field(client, tokens, monkeypatch):
    _patch_generate(monkeypatch, "SELECT nonexistent_col FROM orders")
    r = client.post(
        "/analysis/nl-sql",
        json={"question": "查个不存在的列"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is not None
    assert "执行错误" in body["error"]


# ── provider selection (anthropic / openai-compatible incl. MiniMax) ──────────
def test_openai_provider_not_configured_returns_503(client, tokens, monkeypatch):
    import app.utils.nl_to_sql as nl

    monkeypatch.setattr(nl.settings, "nl_sql_provider", "openai")
    monkeypatch.setattr(nl.settings, "openai_api_key", None)
    r = client.post(
        "/analysis/nl-sql", json={"question": "多少订单"}, headers=_auth(tokens["analyst"])
    )
    assert r.status_code == 503


def test_openai_provider_generates_and_executes(client, tokens, monkeypatch):
    """Real generate_sql dispatch for an OpenAI-compatible provider (MiniMax),
    stubbing only the network call. MiniMax reads its own MINIMAX_API_KEY and
    gets its base_url + default model from the registry."""
    import app.utils.nl_to_sql as nl

    monkeypatch.setattr(nl.settings, "nl_sql_provider", "minimax")
    monkeypatch.setattr(nl.settings, "minimax_api_key", "sk-test")
    monkeypatch.setattr(nl.settings, "nl_sql_model", None)

    async def _fake_openai(question, model, api_key, base_url):
        # registry default model + registry base_url, key from MINIMAX_API_KEY
        assert model == "MiniMax-M2"
        assert base_url == "https://api.minimaxi.com/v1"
        assert api_key == "sk-test"
        return '{"sql": "SELECT 1 AS n", "explanation": "测试"}'

    monkeypatch.setattr(nl, "_complete_openai", _fake_openai)
    r = client.post(
        "/analysis/nl-sql", json={"question": "随便查一行"}, headers=_auth(tokens["analyst"])
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert body["row_count"] == 1
    assert "n" in body["columns"]


def test_request_provider_and_model_override_defaults(client, tokens, monkeypatch):
    """The provider+model in the request body (the UI dropdown) override the
    server defaults and route to the matching key/base_url."""
    import app.utils.nl_to_sql as nl

    # Server default is anthropic, but the request asks for DeepSeek explicitly.
    monkeypatch.setattr(nl.settings, "nl_sql_provider", "anthropic")
    monkeypatch.setattr(nl.settings, "anthropic_api_key", "sk-anthropic")
    monkeypatch.setattr(nl.settings, "deepseek_api_key", "sk-deepseek")
    monkeypatch.setattr(nl.settings, "nl_sql_model", None)

    async def _fake_openai(question, model, api_key, base_url):
        assert model == "deepseek-reasoner"
        assert base_url == "https://api.deepseek.com/v1"
        assert api_key == "sk-deepseek"
        return '{"sql": "SELECT 1 AS n", "explanation": "测试"}'

    monkeypatch.setattr(nl, "_complete_openai", _fake_openai)
    r = client.post(
        "/analysis/nl-sql",
        json={"question": "随便查一行", "provider": "deepseek", "model": "deepseek-reasoner"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["error"] is None


def test_providers_endpoint_lists_only_configured(client, tokens, monkeypatch):
    """GET /analysis/nl-sql/providers returns only providers with a key, default
    provider first."""
    import app.utils.nl_to_sql as nl

    # Configure two providers; anthropic is the default so it sorts first.
    monkeypatch.setattr(nl.settings, "nl_sql_provider", "anthropic")
    monkeypatch.setattr(nl.settings, "anthropic_api_key", "sk-a")
    monkeypatch.setattr(nl.settings, "minimax_api_key", "sk-m")
    monkeypatch.setattr(nl.settings, "deepseek_api_key", None)
    monkeypatch.setattr(nl.settings, "moonshot_api_key", None)
    monkeypatch.setattr(nl.settings, "zhipu_api_key", None)
    monkeypatch.setattr(nl.settings, "openai_api_key", None)

    r = client.get("/analysis/nl-sql/providers", headers=_auth(tokens["analyst"]))
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [p["id"] for p in body["providers"]]
    assert ids[0] == "anthropic"  # default first
    assert set(ids) == {"anthropic", "minimax"}
    assert body["default_provider"] == "anthropic"
    # Each provider advertises its selectable models.
    assert all(p["models"] for p in body["providers"])


def test_providers_endpoint_requires_analyst(client, tokens):
    r = client.get("/analysis/nl-sql/providers", headers=_auth(tokens["viewer"]))
    assert r.status_code == 403


def test_unknown_provider_returns_503(client, tokens, monkeypatch):
    import app.utils.nl_to_sql as nl

    monkeypatch.setattr(nl.settings, "nl_sql_provider", "bogus")
    r = client.post(
        "/analysis/nl-sql", json={"question": "x"}, headers=_auth(tokens["analyst"])
    )
    assert r.status_code == 503


# ── feature disabled when no API key is configured ────────────────────────────
def test_returns_503_when_not_configured(client, tokens, monkeypatch):
    # With every provider key cleared, the real generate_sql finds nothing
    # configured and the endpoint returns 503 (graceful degradation).
    import app.utils.nl_to_sql as nl

    monkeypatch.setattr(nl.settings, "nl_sql_provider", "anthropic")
    for spec in nl.PROVIDERS.values():
        monkeypatch.setattr(nl.settings, spec.key_attr, None)

    r = client.post(
        "/analysis/nl-sql",
        json={"question": "多少订单"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 503

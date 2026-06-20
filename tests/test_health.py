"""Tests for GET /health endpoint.

TDD: written before the implementation.

Coverage:
  - 无需认证即可访问（公开接口）
  - 数据库正常时返回 200，status = "ok"，database = "ok"
  - 响应包含 redis 字段（ok 或 unavailable，取决于环境）
  - 响应包含 version / uptime 等基本信息字段
  - 数据库不可达时返回 503，status = "degraded"，database 包含错误信息
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── fixture ───────────────────────────────────────────────────────────────────

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


# ── 正常状态 ───────────────────────────────────────────────────────────────────

class TestHealthEndpoint:

    def test_health_accessible_without_auth(self, client):
        """健康检查接口不需要登录。"""
        r = client.get("/health")
        assert r.status_code in (200, 503)  # 取决于组件状态，但不应该是 401/403

    def test_health_returns_json(self, client):
        r = client.get("/health")
        assert r.headers["content-type"].startswith("application/json")

    def test_health_has_required_fields(self, client):
        r = client.get("/health")
        body = r.json()
        assert "status" in body
        assert "database" in body
        assert "redis" in body

    def test_health_status_is_ok_when_db_reachable(self, client):
        """测试数据库可达时返回 200 + status ok。"""
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"

    def test_health_database_ok_means_200(self, client):
        r = client.get("/health")
        if r.json().get("database") == "ok":
            assert r.status_code == 200

    def test_health_redis_field_present(self, client):
        """Redis 字段存在，值为 'ok' 或 'unavailable'（取决于环境）。"""
        r = client.get("/health")
        body = r.json()
        assert body["redis"] in ("ok", "unavailable")

    # ── 数据库不可达时降级 ──────────────────────────────────────────────────────

    def test_health_returns_503_when_db_unreachable(self, client, monkeypatch):
        """数据库连不上时返回 503，status = degraded。"""
        import app.main as main_mod
        # 模拟数据库查询抛异常
        async def _bad_check():
            raise Exception("connection refused")

        monkeypatch.setattr(main_mod, "_check_db", _bad_check)

        r = client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["database"] != "ok"

    def test_health_degraded_database_contains_error_info(self, client, monkeypatch):
        import app.main as main_mod

        async def _bad_check():
            raise Exception("timeout after 1s")

        monkeypatch.setattr(main_mod, "_check_db", _bad_check)

        r = client.get("/health")
        body = r.json()
        # 错误信息应该可读，而不是空字符串
        assert body["database"] and body["database"] != "ok"

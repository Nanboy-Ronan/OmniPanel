"""MCP transport, revocation, scopes and shared calculations against isolated Postgres."""
import asyncio
import json
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from test_api_endpoints import client, tokens  # existing isolated DB/auth fixtures

BASE = "/integrations/workbuddy"


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "workbuddy_mcp_enabled", True)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    monkeypatch.setattr(settings, "watchdog_enabled", False)
    monkeypatch.setattr(settings, "weekly_report_enabled", False)
    monkeypatch.setattr(settings, "wechat_auto_sync_enabled", False)


def jwt_headers(tokens, role="analyst"):
    return {"Authorization": "Bearer " + tokens[role]}


def issue(client, tokens, scopes=None, role="analyst"):
    result = client.post(BASE + "/tokens", headers=jwt_headers(tokens, role), json={
        "name": "test connector", "scopes": scopes or ["metadata:read", "analytics:ecommerce", "analytics:media", "reports:read"],
    })
    assert result.status_code == 201, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()


def rpc(client, token, method, params=None, headers=None):
    return client.post(BASE + "/mcp", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25", **(headers or {}),
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def call(client, token, name, arguments=None):
    response = rpc(client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_transport_and_credentials_are_separate(client, tokens):
    issued = issue(client, tokens)
    response = rpc(client, issued["token"], "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
    })
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"
    listed = rpc(client, issued["token"], "tools/list").json()["result"]["tools"]
    assert len(listed) == 11
    assert all(t["annotations"]["readOnlyHint"] for t in listed)
    assert rpc(client, tokens["admin"], "tools/list").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + issued["token"]}).status_code == 401
    assert rpc(client, "rpa_wb_invalid", "tools/list").status_code == 401
    assert rpc(client, issued["token"], "tools/list", headers={"Origin": "https://evil.example"}).status_code == 403
    assert rpc(client, issued["token"], "tools/list", headers={"Host": "evil.example"}).status_code == 421


def test_scopes_are_enforced_and_no_sql_tool_exists(client, tokens):
    issued = issue(client, tokens, ["metadata:read"])
    result = call(client, issued["token"], "get_metric_definitions")
    assert not result.get("isError")
    denied = call(client, issued["token"], "query_ecommerce_metrics", {"start_date": "2026-09-01", "end_date": "2026-09-30"})
    assert denied["isError"] is True
    assert call(client, issued["token"], "execute_sql", {"sql": "select * from user"})["isError"]


def test_token_lifecycle_is_owner_scoped_and_hash_only(client, tokens, pg_sync_url):
    issued = issue(client, tokens)
    listed = client.get(BASE + "/tokens", headers=jwt_headers(tokens)).json()
    assert len(listed) == 1 and "token" not in listed[0] and "token_hash" not in listed[0]
    assert client.get(BASE + "/tokens", headers=jwt_headers(tokens, "admin")).json() == []
    assert client.delete(BASE + "/tokens/" + issued["id"], headers=jwt_headers(tokens, "admin")).status_code == 404
    engine = create_engine(pg_sync_url)
    with engine.connect() as connection:
        hashed = connection.execute(text("SELECT token_hash FROM integration_tokens")).scalar_one()
        assert len(hashed) == 64 and issued["token"] != hashed
        logs = connection.execute(text("SELECT detail FROM operation_log WHERE action LIKE 'workbuddy%'")).scalars().all()
        assert issued["token"] not in str(logs)
    engine.dispose()
    assert client.delete(BASE + "/tokens/" + issued["id"], headers=jwt_headers(tokens)).status_code == 204
    assert rpc(client, issued["token"], "tools/list").status_code == 401


@pytest.mark.parametrize("change", ["disabled", "downgraded", "expired"])
def test_account_changes_and_expiry_take_effect_immediately(client, tokens, pg_sync_url, change):
    issued = issue(client, tokens)
    engine = create_engine(pg_sync_url)
    with engine.begin() as connection:
        if change == "disabled":
            connection.execute(text('UPDATE "user" SET is_active=false WHERE email=\'analyst@test.com\''))
        elif change == "downgraded":
            connection.execute(text('UPDATE "user" SET role=\'viewer\' WHERE email=\'analyst@test.com\''))
        else:
            connection.execute(text("UPDATE integration_tokens SET expires_at=now()-interval '1 second'"))
    engine.dispose()
    assert rpc(client, issued["token"], "tools/list").status_code == 401


def test_viewer_cannot_issue_token_or_choose_write_scope(client, tokens):
    body = {"name": "test", "scopes": ["metadata:read"]}
    assert client.post(BASE + "/tokens", headers=jwt_headers(tokens, "viewer"), json=body).status_code == 403
    for update in [{"scopes": ["admin:write"]}, {"expires_in_days": 91}, {"user_id": "another-user"}, {"name": "   "}]:
        assert client.post(BASE + "/tokens", headers=jwt_headers(tokens), json={**body, **update}).status_code == 422


def test_disabled_integration_still_allows_revocation(client, tokens, monkeypatch):
    from app.config import settings
    issued = issue(client, tokens)
    monkeypatch.setattr(settings, "workbuddy_mcp_enabled", False)
    assert rpc(client, issued["token"], "tools/list").status_code == 401
    assert client.post(BASE + "/tokens", headers=jwt_headers(tokens), json={"name": "test", "scopes": ["metadata:read"]}).status_code == 503
    assert client.delete(BASE + "/tokens/" + issued["id"], headers=jwt_headers(tokens)).status_code == 204


def test_queries_match_dashboard_and_never_expose_personal_data(client, tokens, pg_sync_url):
    import app.db as db
    from app.db.models import Customer, Order, MediaAccount, MediaPost, MediaPostMetricDaily, XhsAccount, XhsPost
    async def seed():
        async with db.AsyncSessionLocal() as s:
            s.add(Customer(customer_key="secret-phone", platform="youzan", first_order_date=date(2026, 9, 1)))
            await s.flush()
            s.add_all([
                Order(order_id="paid", customer_key="secret-phone", platform="youzan", order_date=date(2026, 9, 1), price=100, refunded_amount=20, status_group="completed", receiver="secret-name", full_address="secret-address"),
                Order(order_id="cancelled", customer_key="secret-phone", platform="youzan", order_date=date(2026, 9, 1), price=200, status_group="closed"),
            ])
            account = MediaAccount(platform="wechat_official", name="test", app_id="fake")
            xhs = XhsAccount(name="test-xhs")
            s.add_all([account, xhs]); await s.flush()
            post = MediaPost(account_id=account.id, platform="wechat_official", external_id="post", title="test", publish_date=date(2026, 9, 1))
            s.add(post); await s.flush()
            s.add_all([
                MediaPostMetricDaily(post_id=post.id, metric_date=date(2026, 9, 1), read_user_count=10),
                MediaPostMetricDaily(post_id=post.id, metric_date=date(2026, 9, 2), read_user_count=30),
                *[XhsPost(account_id=xhs.id, title=f"post-{i}", publish_date=date(2026, 9, 1), views=2) for i in range(501)],
            ])
            await s.commit()
    asyncio.run(seed())
    issued = issue(client, tokens)
    args = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    result = call(client, issued["token"], "query_ecommerce_metrics", args)
    assert not result.get("isError"), result
    content = result["structuredContent"]
    dashboard = client.get("/analysis/dashboard", headers=jwt_headers(tokens), params=args).json()
    assert content["data"] == dashboard
    assert content["data"]["current"]["revenue"] == 80
    assert all(secret not in json.dumps(result) for secret in ["secret-phone", "secret-name", "secret-address"])
    for source, endpoint in [("wechat", "/media/overview"), ("xhs", "/media/xhs/overview"), ("channels", "/media/channels/overview")]:
        result = call(client, issued["token"], "query_media_metrics", {**args, "source": source})
        assert not result.get("isError"), result
        content = result["structuredContent"]
        expected = client.get(endpoint, headers=jwt_headers(tokens), params=args).json()
        assert content["data"] == expected
        if source == "wechat": assert content["data"]["read_user_count"] == 30
        if source == "xhs": assert content["data"]["views"] == 1002
        assert "definition" in content and "freshness" in content
    result = call(client, issued["token"], "get_data_freshness")
    assert result["structuredContent"]["data"] == client.get("/data/freshness", headers=jwt_headers(tokens)).json()
    assert call(client, issued["token"], "get_weekly_report")["structuredContent"]["data"] is None
    engine = create_engine(pg_sync_url)
    with engine.connect() as c:
        details = c.execute(text("SELECT detail FROM operation_log WHERE action='workbuddy_query'")).scalars().all()
        assert len(details) == 6
        assert all(json.loads(d)["outcome"] == "success" for d in details)
    engine.dispose()


def test_bad_arguments_are_tool_errors(client, tokens):
    raw = issue(client, tokens)["token"]
    for arguments in [
        {"start_date": "2026-09-30", "end_date": "2026-09-01"},
        {"start_date": "2020-01-01", "end_date": "2026-09-01"},
        {"start_date": "2026-09-01", "end_date": "2026-09-02", "platform": "invalid"},
    ]:
        assert call(client, raw, "query_ecommerce_metrics", arguments)["isError"]
    assert call(client, raw, "get_weekly_report", {"week_start": "2026-09-01"})["isError"]


def test_audit_failure_does_not_return_data(client, tokens, monkeypatch):
    from app.integrations.workbuddy import server
    async def fail(*args, **kwargs):
        raise RuntimeError("private detail")
    monkeypatch.setattr(server, "log_operation", fail)
    result = call(client, issue(client, tokens)["token"], "get_metric_definitions")
    assert result["isError"] and "structuredContent" not in result
    assert "private detail" not in json.dumps(result)


def _analyst_id(client, tokens):
    users = client.get("/admin/users", headers=jwt_headers(tokens, "admin")).json()
    return next(u["id"] for u in users if u["email"] == "analyst@test.com")


@pytest.mark.parametrize("change", ["active", "role"])
def test_disable_or_downgrade_revokes_so_restoring_does_not_revive(client, tokens, change):
    issued = issue(client, tokens)
    admin, user_id = jwt_headers(tokens, "admin"), _analyst_id(client, tokens)
    if change == "active":
        assert client.put(f"/admin/users/{user_id}/active", json={"is_active": False}, headers=admin).status_code == 200
        assert client.put(f"/admin/users/{user_id}/active", json={"is_active": True}, headers=admin).status_code == 200
    else:
        assert client.put(f"/admin/users/{user_id}/role", json={"role": "viewer"}, headers=admin).status_code == 200
        assert client.put(f"/admin/users/{user_id}/role", json={"role": "analyst"}, headers=admin).status_code == 200
    assert rpc(client, issued["token"], "tools/list").status_code == 401
    listed = client.get(BASE + "/tokens", headers=jwt_headers(tokens)).json()
    assert listed[0]["revoked_at"] is not None


def test_admin_promotion_keeps_credentials(client, tokens):
    issued = issue(client, tokens)
    user_id = _analyst_id(client, tokens)
    assert client.put(f"/admin/users/{user_id}/role", json={"role": "admin"}, headers=jwt_headers(tokens, "admin")).status_code == 200
    assert rpc(client, issued["token"], "tools/list").status_code == 200


def test_admin_lists_and_revokes_any_connection(client, tokens, pg_sync_url):
    issued = issue(client, tokens)
    path = "/admin/integrations/workbuddy/tokens"
    assert client.get(path, headers=jwt_headers(tokens)).status_code == 403
    listed = client.get(path, headers=jwt_headers(tokens, "admin")).json()
    assert [(t["id"], t["user_email"], t["kind"]) for t in listed] == [(issued["id"], "analyst@test.com", "token")]
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    assert client.delete(path + "/" + issued["id"], headers=jwt_headers(tokens)).status_code == 403
    assert client.delete(path + "/" + issued["id"], headers=jwt_headers(tokens, "admin")).status_code == 204
    assert rpc(client, issued["token"], "tools/list").status_code == 401
    assert client.get(path, headers=jwt_headers(tokens, "admin")).json() == []
    assert len(client.get(path + "?include_inactive=true", headers=jwt_headers(tokens, "admin")).json()) == 1
    engine = create_engine(pg_sync_url)
    with engine.connect() as connection:
        detail = connection.execute(text("SELECT detail FROM operation_log WHERE action='workbuddy_token_admin_revoked'")).scalar_one()
    engine.dispose()
    assert '"target_user"' in detail and '"ip"' in detail


def test_tool_calls_record_client_ip_and_are_throttled_per_credential(client, tokens, pg_sync_url, monkeypatch):
    from app.integrations.workbuddy import server
    from app.utils.rate_limiter import LoginRateLimiter
    monkeypatch.setattr(server, "tool_rate_limiter", LoginRateLimiter(max_attempts=2))
    issued, other = issue(client, tokens), issue(client, tokens)
    for _ in range(2):
        assert not call(client, issued["token"], "get_metric_definitions").get("isError")
    limited = call(client, issued["token"], "get_metric_definitions")
    assert limited.get("isError") and "过于频繁" in limited["content"][0]["text"]
    assert not call(client, other["token"], "get_metric_definitions").get("isError")
    engine = create_engine(pg_sync_url)
    with engine.connect() as connection:
        details = [json.loads(d) for d in connection.execute(text(
            "SELECT detail FROM operation_log WHERE action='workbuddy_query' ORDER BY id")).scalars()]
    engine.dispose()
    assert all(d.get("ip") for d in details)
    assert [d["outcome"] for d in details].count("rate_limited") == 1


def test_retention_purges_only_expired_state(client, tokens, pg_sync_url):
    from datetime import datetime, timedelta, timezone
    from app import db
    from app.integrations.workbuddy.retention import purge_once
    keep, drop = issue(client, tokens), issue(client, tokens)
    engine = create_engine(pg_sync_url)
    with engine.begin() as connection:
        connection.execute(text("UPDATE integration_tokens SET revoked_at=now()-interval '31 days' WHERE id=:id"), {"id": drop["id"]})
        connection.execute(text("INSERT INTO workbuddy_oauth_clients (id, redirect_uris, created_at) VALUES "
                                "('stale', '[]', now()-interval '2 days'), ('fresh', '[]', now())"))
    async def run():
        async with db.AsyncSessionLocal() as session:
            return await purge_once(session, datetime.now(timezone.utc))
    counts = asyncio.run(run())
    with engine.connect() as connection:
        ids = set(connection.execute(text("SELECT id::text FROM integration_tokens")).scalars())
        clients = set(connection.execute(text("SELECT id FROM workbuddy_oauth_clients")).scalars())
    engine.dispose()
    assert ids == {keep["id"]} and clients == {"fresh"}
    assert counts["credentials"] == 1 and counts["clients"] == 1


def test_stale_repair_candidates_are_removed(tmp_path):
    from app.integrations.workbuddy.retention import purge_repair_candidates
    old, new, real = tmp_path / ".repair-a.json", tmp_path / ".repair-b.json", tmp_path / "xhs_1.json"
    for path in (old, new, real):
        path.write_text("{}")
    import os
    os.utime(old, (0, 0))
    os.utime(real, (0, 0))
    assert purge_repair_candidates(tmp_path) == 1
    assert not old.exists() and new.exists() and real.exists()

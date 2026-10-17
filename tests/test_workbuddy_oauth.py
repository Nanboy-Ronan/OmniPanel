"""Public OAuth/PKCE through HTTP, using a fresh isolated PostgreSQL database."""
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest
from app.config import settings
from sqlalchemy import create_engine, text
from test_workbuddy import client, tokens, enabled, jwt_headers, rpc

BASE = "/integrations/workbuddy/oauth"
REDIRECT = "workbuddy://workbuddy/mcp/connector%3AOmniPanel-oauth/oauth/callback"
VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


@pytest.fixture(autouse=True)
def no_throttling(monkeypatch):
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr("app.views.workbuddy_oauth.throttle", noop)


def begin(client, **overrides):
    r = client.post(BASE + "/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"})
    assert r.status_code == 201, r.text
    c = r.json()["client_id"]
    params = {"client_id": c, "redirect_uri": REDIRECT, "response_type": "code", "state": "test-state",
              "code_challenge": CHALLENGE, "code_challenge_method": "S256", **overrides}
    r = client.get(BASE + "/authorize", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    rid = parse_qs(urlsplit(r.headers["location"]).query)["authorization"][0]
    return c, rid


def authorize(client, tokens, role="analyst", **kwargs):
    c, rid = begin(client, **kwargs)
    r = client.post(BASE + "/requests/" + rid, headers=jwt_headers(tokens, role), json={"approve": True})
    assert r.status_code == 200, r.text
    q = parse_qs(urlsplit(r.json()["redirect_url"]).query)
    assert q["state"] == ["test-state"]
    return {"grant_type": "authorization_code", "client_id": c, "code": q["code"][0], "redirect_uri": REDIRECT, "code_verifier": VERIFIER}


def exchange(client, data):
    r = client.post(BASE + "/token", data=data)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def test_oauth_discovery(client):
    m = client.get("/.well-known/oauth-protected-resource").json()
    assert m["authorization_servers"] == ["http://testserver"]
    assert m["resource"] == "http://testserver/api/integrations/workbuddy/mcp"
    a = client.get("/.well-known/oauth-authorization-server").json()
    assert a["code_challenge_methods_supported"] == ["S256"]
    assert "resource_metadata=" in rpc(client, "invalid", "tools/list").headers["www-authenticate"]


def test_roundtrip_pkce_rotation_replay_and_hash_storage(client, tokens, pg_sync_url):
    data = authorize(client, tokens, scope="metadata:read")
    assert client.post(BASE + "/token", data={**data, "code_verifier": "b" * 64}).json()["error"] == "invalid_grant"
    assert client.post(BASE + "/token", data={**data, "redirect_uri": REDIRECT + "/"}).status_code == 400
    assert client.post(BASE + "/token", data={**data, "client_id": "other"}).status_code == 400
    first = exchange(client, data)
    assert rpc(client, first["access_token"], "tools/list").status_code == 200
    refresh_data = {"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": data["client_id"]}
    assert client.post(BASE + "/token", data={**refresh_data, "client_id": "other"}).status_code == 400
    second = exchange(client, refresh_data)
    assert first["refresh_token"] != second["refresh_token"]
    assert rpc(client, first["access_token"], "tools/list").status_code == 401
    assert rpc(client, second["access_token"], "tools/list").status_code == 200
    # Used refresh tokens invalidate the whole family, including the new access token.
    assert client.post(BASE + "/token", data=refresh_data).status_code == 400
    assert rpc(client, second["access_token"], "tools/list").status_code == 401
    engine = create_engine(pg_sync_url)
    with engine.connect() as conn:
        dump = str(conn.execute(text("SELECT * FROM workbuddy_oauth_refresh")).all())
        dump += str(conn.execute(text("SELECT * FROM workbuddy_oauth_requests")).all())
        dump += str(conn.execute(text("SELECT * FROM integration_tokens")).all())
        dump += str(conn.execute(text("SELECT detail FROM operation_log")).all())
        for raw in [data["code"], first["refresh_token"], second["access_token"]]:
            assert raw not in dump
    engine.dispose()


@pytest.mark.parametrize("uri", ["https://evil.example/callback", "http://localhost:1234/oauth/callback", "http://127.0.0.1:1234/oauth/callback?evil=1", "workbuddy://evil/mcp/connector%3Ax/oauth/callback", "http://127.0.0.1:1234@evil.example/oauth/callback"])
def test_bad_redirects_rejected(client, uri, monkeypatch):
    assert client.post(BASE + "/register", json={"redirect_uris": [uri]}).status_code == 400
    monkeypatch.setattr(settings, "workbuddy_web_callback_url", uri)
    r = client.post(BASE + "/register", json={"redirect_uris": [uri]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


def test_consent_requires_login_role_and_one_decision(client, tokens):
    c, rid = begin(client)
    path = BASE + "/requests/" + rid
    assert client.post(path, json={"approve": True}).status_code == 401
    assert client.get(path, headers=jwt_headers(tokens, "viewer")).json()["can_authorize"] is False
    assert client.post(path, headers=jwt_headers(tokens, "viewer"), json={"approve": True}).status_code == 403
    r = client.post(path, headers=jwt_headers(tokens), json={"approve": False})
    assert "error=access_denied" in r.json()["redirect_url"]
    assert client.post(path, headers=jwt_headers(tokens), json={"approve": True}).status_code == 400


@pytest.mark.parametrize("change", ["disabled", "role", "revoke", "expiry"])
def test_refresh_checks_current_account_and_revocation(client, tokens, pg_sync_url, change):
    data = authorize(client, tokens)
    pair = exchange(client, data)
    if change == "revoke":
        listed = client.get("/integrations/workbuddy/tokens", headers=jwt_headers(tokens)).json()
        assert client.delete("/integrations/workbuddy/tokens/" + listed[0]["id"], headers=jwt_headers(tokens, "admin")).status_code == 404
        assert client.delete("/integrations/workbuddy/tokens/" + listed[0]["id"], headers=jwt_headers(tokens)).status_code == 204
    else:
        statements = {"disabled": 'UPDATE "user" SET is_active=false WHERE email=\'analyst@test.com\'',
                      "role": 'UPDATE "user" SET role=\'viewer\' WHERE email=\'analyst@test.com\'',
                      "expiry": "UPDATE workbuddy_oauth_grants SET expires_at=now()-interval '1 second'"}
        engine = create_engine(pg_sync_url)
        with engine.begin() as conn:
            conn.execute(text(statements[change]))
        engine.dispose()
    assert client.post(BASE + "/token", data={"grant_type": "refresh_token", "client_id": data["client_id"], "refresh_token": pair["refresh_token"]}).status_code == 400
    if change != "expiry":
        assert rpc(client, pair["access_token"], "tools/list").status_code == 401


def test_resource_and_scope_boundaries(client, tokens):
    data = authorize(client, tokens)
    assert client.post(BASE + "/token", data={**data, "resource": "https://evil.example"}).status_code == 400
    c, _ = begin(client)
    base = {"client_id": c, "redirect_uri": REDIRECT, "response_type": "code", "code_challenge": CHALLENGE, "code_challenge_method": "S256"}
    for update in [{"scope": "admin:write"}, {"resource": "https://evil.example"}, {"code_challenge_method": "plain"}, {"redirect_uri": "https://evil.example"}]:
        r = client.get(BASE + "/authorize", params={**base, **update}, follow_redirects=False)
        assert r.status_code == 400 and "location" not in r.headers


def test_expired_code_and_revoke_endpoint(client, tokens, pg_sync_url):
    data = authorize(client, tokens)
    engine = create_engine(pg_sync_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE workbuddy_oauth_requests SET expires_at=now()-interval '1 second'"))
    assert client.post(BASE + "/token", data=data).status_code == 400
    data = authorize(client, tokens)
    pair = exchange(client, data)
    assert client.post(BASE + "/revoke", data={"client_id": "other", "token": pair["refresh_token"]}).status_code == 200
    assert rpc(client, pair["access_token"], "tools/list").status_code == 200
    assert client.post(BASE + "/revoke", data={"client_id": data["client_id"], "token": pair["refresh_token"]}).status_code == 200
    assert rpc(client, pair["access_token"], "tools/list").status_code == 401
    engine.dispose()


def test_parallel_code_exchange_is_single_use(client, tokens):
    from concurrent.futures import ThreadPoolExecutor
    data = authorize(client, tokens)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(BASE + "/token", data=data), range(2)))
    assert sorted(r.status_code for r in results) == [200, 400]


def test_parallel_refresh_detects_replay(client, tokens):
    from concurrent.futures import ThreadPoolExecutor
    data = authorize(client, tokens)
    pair = exchange(client, data)
    refresh_data = {"grant_type": "refresh_token", "client_id": data["client_id"], "refresh_token": pair["refresh_token"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(BASE + "/token", data=refresh_data), range(2)))
    assert sorted(r.status_code for r in results) == [200, 400]
    new = next(r.json()["access_token"] for r in results if r.status_code == 200)
    assert rpc(client, new, "tools/list").status_code == 401


def test_malformed_registration_and_loopback(client):
    for bad in [[], {"redirect_uris": []}, {"redirect_uris": [REDIRECT], "grant_types": [{}]}]:
        assert client.post(BASE + "/register", json=bad).status_code == 400
    r = client.post(BASE + "/register", json={"redirect_uris": ["http://127.0.0.1:54321/oauth/callback"]})
    assert r.status_code == 201 and r.json()["redirect_uris"] == ["http://127.0.0.1:54321/oauth/callback"]


def test_two_colleagues_keep_separate_grants_and_queries(client, tokens):
    a = authorize(client, tokens, role="analyst", scope="metadata:read")
    b = authorize(client, tokens, role="admin", scope="metadata:read")
    pa, pb = exchange(client, a), exchange(client, b)
    assert pa["access_token"] != pb["access_token"]
    assert len(client.get("/integrations/workbuddy/tokens", headers=jwt_headers(tokens)).json()) == 1
    assert len(client.get("/integrations/workbuddy/tokens", headers=jwt_headers(tokens, "admin")).json()) == 1
    client.post(BASE + "/revoke", data={"client_id": a["client_id"], "token": pa["access_token"]})
    assert rpc(client, pa["access_token"], "tools/list").status_code == 401
    assert rpc(client, pb["access_token"], "tools/list").status_code == 200


def test_official_web_callback_registration(client, monkeypatch):
    uri = "https://www.workbuddy.cn/console/as/p/connector/oauth/enterprise_OmniPanel-oauth/connect"
    assert client.post(BASE + "/register", json={"redirect_uris": [uri]}).status_code == 400
    monkeypatch.setattr(settings, "workbuddy_web_callback_url", uri)
    r = client.post(BASE + "/register", json={"redirect_uris": [uri], "token_endpoint_auth_method": "none"})
    assert r.status_code == 201
    assert r.json()["redirect_uris"] == [uri]
    for bad in [uri + "/", uri + "?next=https://evil.example", uri + "#fragment",
                uri.replace("https:", "http:"), uri.replace("www.workbuddy.cn", "www.workbuddy.cn.evil.example"),
                uri.replace("www.workbuddy.cn", "evil.example@www.workbuddy.cn"),
                uri.replace("enterprise_OmniPanel-oauth", "other-connector")]:
        result = client.post(BASE + "/register", json={"redirect_uris": [bad]})
        assert result.status_code == 400
        assert result.json()["error"] == "invalid_redirect_uri"


def test_web_oauth_roundtrip(client, tokens, monkeypatch):
    monkeypatch.setattr(settings, "workbuddy_web_callback_url", "https://www.workbuddy.cn/console/as/p/connector/oauth/enterprise_OmniPanel-oauth/connect")
    monkeypatch.setitem(globals(), 'REDIRECT', 'https://www.workbuddy.cn/console/as/p/connector/oauth/enterprise_OmniPanel-oauth/connect')
    data = authorize(client, tokens, scope='metadata:read')
    issued = exchange(client, data)
    assert rpc(client, issued['access_token'], 'tools/list').status_code == 200
    assert client.post(BASE + '/token', data=data).status_code == 400


def test_replayed_code_revokes_issued_token_and_is_audited(client, tokens, pg_sync_url):
    data = authorize(client, tokens, scope="metadata:read")
    issued = exchange(client, data)
    assert client.post(BASE + "/token", data=data).json()["error"] == "invalid_grant"
    assert rpc(client, issued["access_token"], "tools/list").status_code == 401
    refresh = {"grant_type": "refresh_token", "client_id": data["client_id"], "refresh_token": issued["refresh_token"]}
    assert client.post(BASE + "/token", data=refresh).status_code == 400
    engine = create_engine(pg_sync_url)
    with engine.connect() as conn:
        actions = [r[0] for r in conn.execute(text("SELECT action FROM operation_log ORDER BY id"))]
        detail = conn.execute(text("SELECT detail FROM operation_log WHERE action='workbuddy_oauth_connected'")).scalar_one()
    engine.dispose()
    assert actions.count("workbuddy_oauth_replay_revoked") == 1
    assert {"workbuddy_oauth_registered", "workbuddy_oauth_consent", "workbuddy_oauth_connected"} <= set(actions)
    assert '"ip"' in detail and "桌面客户端" in detail


def test_refresh_replay_and_revoke_endpoint_are_audited(client, tokens, pg_sync_url):
    data = authorize(client, tokens)
    pair = exchange(client, data)
    refresh = {"grant_type": "refresh_token", "client_id": data["client_id"], "refresh_token": pair["refresh_token"]}
    exchange(client, refresh)
    assert client.post(BASE + "/token", data=refresh).status_code == 400
    other = exchange(client, authorize(client, tokens))
    client.post(BASE + "/revoke", data={"client_id": data["client_id"], "token": pair["refresh_token"]})
    engine = create_engine(pg_sync_url)
    with engine.connect() as conn:
        actions = [r[0] for r in conn.execute(text("SELECT action FROM operation_log"))]
    engine.dispose()
    assert actions.count("workbuddy_oauth_replay_revoked") == 1
    assert rpc(client, other["access_token"], "tools/list").status_code == 200


def test_consent_page_shows_which_client_and_when(client, tokens):
    _, rid = begin(client)
    details = client.get(BASE + "/requests/" + rid, headers=jwt_headers(tokens)).json()
    assert details["client"] == "WorkBuddy 桌面客户端"
    assert details["requested_at"] and details["expires_at"] > details["requested_at"]


def test_token_and_revoke_endpoints_are_throttled(client, monkeypatch):
    from fastapi import HTTPException
    from app.views import workbuddy_oauth
    calls = []
    async def strict(request, kind, key=None, limiter=None):
        calls.append((kind, key))
        raise HTTPException(429)
    monkeypatch.setattr(workbuddy_oauth, "throttle", strict)
    assert client.post(BASE + "/token", data={"grant_type": "refresh_token", "client_id": "c1", "refresh_token": "x"}).status_code == 429
    assert client.post(BASE + "/revoke", data={"client_id": "c1", "token": "x"}).status_code == 429
    assert calls == [("token", "c1"), ("revoke", None)]


def test_retention_keeps_oauth_credential_while_grant_is_live(client, tokens, pg_sync_url):
    import asyncio
    from datetime import datetime, timezone
    from app import db
    from app.integrations.workbuddy.retention import purge_once
    data = authorize(client, tokens)
    pair = exchange(client, data)
    engine = create_engine(pg_sync_url)
    with engine.begin() as conn:
        # Access token long expired, but the 90-day grant still allows refresh.
        conn.execute(text("UPDATE integration_tokens SET expires_at=now()-interval '31 days'"))
    async def run():
        async with db.AsyncSessionLocal() as session:
            return await purge_once(session, datetime.now(timezone.utc))
    assert asyncio.run(run())["credentials"] == 0
    refresh = {"grant_type": "refresh_token", "client_id": data["client_id"], "refresh_token": pair["refresh_token"]}
    renewed = exchange(client, refresh)
    with engine.begin() as conn:
        conn.execute(text("UPDATE integration_tokens SET expires_at=now()-interval '31 days'"))
        conn.execute(text("UPDATE workbuddy_oauth_grants SET expires_at=now()-interval '31 days'"))
    assert asyncio.run(run())["credentials"] == 1
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM workbuddy_oauth_grants")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM workbuddy_oauth_refresh")).scalar_one() == 0
    engine.dispose()
    assert rpc(client, renewed["access_token"], "tools/list").status_code == 401

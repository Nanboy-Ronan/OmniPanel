"""WorkBuddy public-client OAuth. Consent uses the console's authenticated bearer,
never a caller-supplied user ID. Codes and refresh tokens are hash-only at rest.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user
from ..config import settings
from ..db import get_session
from ..db.models import (IntegrationToken, OperationLog, User, WorkBuddyOAuthClient as Client,
                         WorkBuddyOAuthRequest as Authorization, WorkBuddyOAuthGrant as Grant,
                         WorkBuddyOAuthRefresh as Refresh)
from ..integrations.workbuddy.auth import SCOPES, token_hash
from ..utils.rate_limiter import get_client_ip, LoginRateLimiter

oauth_rate_limiter = LoginRateLimiter(max_attempts=60)

router = APIRouter(tags=["workbuddy-oauth"])
PREFIX = "/integrations/workbuddy/oauth"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


def now():
    return datetime.now(timezone.utc)


def issuer():
    return settings.public_origin


def resource():
    return issuer() + "/api/integrations/workbuddy/mcp"


def endpoint(path):
    return issuer() + "/api" + PREFIX + path


def error(code="invalid_request", description="请求无效，请从 WorkBuddy 重新连接。", status=400):
    return JSONResponse({"error": code, "error_description": description}, status_code=status, headers=NO_STORE)


def enabled():
    if not settings.workbuddy_mcp_enabled:
        raise HTTPException(503, "WorkBuddy 接入尚未启用")


async def throttle(request, kind):
    # Count all public registrations/authorization starts, not only failures.
    key = get_client_ip(request)
    await oauth_rate_limiter.check(key, "workbuddy_oauth_" + kind)
    await oauth_rate_limiter.record_failure(key, "workbuddy_oauth_" + kind)


def valid_redirect(value):
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    # Official web callback observed during enterprise connector registration.
    # Exact URL only: no arbitrary WorkBuddy paths, queries, or subdomains.
    if (value == settings.workbuddy_web_callback_url and
            re.fullmatch(r"https://www\.workbuddy\.cn/console/as/p/connector/oauth/[A-Za-z0-9_-]+/connect", value)):
        return True
    # Documented desktop callback and native loopback fallback.
    if re.fullmatch(r"workbuddy://workbuddy/mcp/connector(?:%3[Aa]|:)[A-Za-z0-9_-]+/oauth/callback", value):
        return True
    try:
        u = urlsplit(value)
        return (u.scheme == "http" and u.hostname == "127.0.0.1" and u.port is not None
                and 1 <= u.port <= 65535 and u.netloc == f"127.0.0.1:{u.port}"
                and u.path == "/oauth/callback" and not u.query and not u.fragment)
    except ValueError:
        return False


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/api/integrations/workbuddy/mcp")
async def resource_metadata():
    enabled()
    return {"resource": resource(), "authorization_servers": [issuer()],
            "scopes_supported": list(SCOPES), "bearer_methods_supported": ["header"]}


@router.get("/.well-known/oauth-authorization-server")
async def authorization_metadata():
    enabled()
    return {"issuer": issuer(), "authorization_endpoint": endpoint("/authorize"),
            "token_endpoint": endpoint("/token"), "registration_endpoint": endpoint("/register"),
            "revocation_endpoint": endpoint("/revoke"), "scopes_supported": list(SCOPES),
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"]}


async def payload(request, form=False):
    if len(await request.body()) > 8192:
        return None
    try:
        if form:
            from urllib.parse import parse_qsl
            pairs = parse_qsl((await request.body()).decode(), max_num_fields=20)
            if len(dict(pairs)) != len(pairs):
                return None
            return dict(pairs)
        result = await request.json()
        return result if isinstance(result, dict) else None
    except (ValueError, UnicodeError):
        return None


@router.post(PREFIX + "/register")
async def register(request: Request, session: AsyncSession = Depends(get_session)):
    enabled()
    await throttle(request, "register")
    body = await payload(request)
    if body is None:
        return error()
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= 5 or not all(valid_redirect(u) for u in uris):
        # Log only callback routing, never query values or credentials.
        routes = []
        for uri in uris[:5] if isinstance(uris, list) else []:
            try:
                parsed = urlsplit(uri) if isinstance(uri, str) else None
                if parsed:
                    routes.append((parsed.scheme[:32], (parsed.hostname or "")[:128], parsed.path[:256]))
            except ValueError:
                pass
        logging.getLogger(__name__).warning("workbuddy_oauth_redirect_rejected routes=%r", routes)
        return error("invalid_redirect_uri")
    if (body.get("token_endpoint_auth_method", "none") != "none"
            or body.get("response_types", ["code"]) != ["code"]
            or not isinstance(body.get("grant_types", []), list)
            or not all(isinstance(g, str) for g in body.get("grant_types", []))
            or not set(body.get("grant_types", [])).issubset({"authorization_code", "refresh_token"})):
        return error("invalid_client_metadata")
    client_id = secrets.token_urlsafe(24)
    session.add(Client(id=client_id, redirect_uris=uris))
    await session.commit()
    return JSONResponse({"client_id": client_id, "client_id_issued_at": int(now().timestamp()),
                         "redirect_uris": uris, "token_endpoint_auth_method": "none",
                         "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]},
                        status_code=201, headers=NO_STORE)


@router.get(PREFIX + "/authorize")
async def authorize(request: Request, session: AsyncSession = Depends(get_session)):
    enabled()
    p = request.query_params
    if len(p.multi_items()) != len(p) or len(str(p)) > 8192:
        return error()
    client = await session.get(Client, p.get("client_id", ""))
    if client is None or p.get("redirect_uri") not in client.redirect_uris:
        return error()  # Never redirect errors to an unvalidated destination.
    if (p.get("response_type") != "code" or p.get("code_challenge_method") != "S256"
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", p.get("code_challenge", ""))
            or len(p.get("state", "")) > 2048 or p.get("resource", resource()) != resource()):
        return error()
    scopes = p.get("scope", " ".join(SCOPES)).split()
    if not scopes or not set(scopes).issubset(SCOPES):
        return error("invalid_scope")
    await throttle(request, "authorize")
    row = Authorization(id=secrets.token_urlsafe(32), client_id=client.id,
                        redirect_uri=p["redirect_uri"], state=p.get("state", ""), challenge=p["code_challenge"],
                        scopes=sorted(set(scopes)), expires_at=now() + timedelta(minutes=10))
    session.add(row)
    await session.commit()
    return RedirectResponse(issuer() + "/console/?" + urlencode({"page": "workbuddy", "authorization": row.id}),
                            status_code=302, headers=NO_STORE)


async def pending(session, request_id, lock=False):
    query = select(Authorization).where(Authorization.id == request_id)
    row = (await session.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if row is None or row.expires_at <= now() or row.decided_at is not None:
        raise HTTPException(400, "连接请求已过期，请返回 WorkBuddy 重新连接。")
    return row


@router.get(PREFIX + "/requests/{request_id}")
async def consent_details(request_id: str, user=Depends(current_active_user), session: AsyncSession = Depends(get_session)):
    enabled()
    row = await pending(session, request_id)
    return JSONResponse({"scopes": row.scopes, "can_authorize": user.role in {"analyst", "admin"}}, headers=NO_STORE)


class Decision(BaseModel):
    approve: bool


@router.post(PREFIX + "/requests/{request_id}")
async def consent(request_id: str, body: Decision, user=Depends(current_active_user), session: AsyncSession = Depends(get_session)):
    enabled()
    row = await pending(session, request_id, lock=True)
    if body.approve and user.role not in {"analyst", "admin"}:
        raise HTTPException(403, "请联系管理员开通分析师权限。")
    row.decided_at = now()
    result = {"state": row.state}
    if body.approve:
        code = secrets.token_urlsafe(32)
        row.user_id, row.code_hash = user.id, token_hash(code)
        result["code"] = code
    else:
        result["error"] = "access_denied"
    session.add(OperationLog(user_id=user.id, action="workbuddy_oauth_consent", detail=json.dumps({"approved": body.approve, "scopes": row.scopes})))
    await session.commit()
    return JSONResponse({"redirect_url": row.redirect_uri + "?" + urlencode(result)}, headers=NO_STORE)


async def issue(session, credential, grant):
    access, refresh = "rpa_wb_" + secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    credential.token_hash = token_hash(access)
    credential.expires_at = now() + timedelta(hours=1)
    session.add(Refresh(token_hash=token_hash(refresh), grant_id=grant.token_id))
    await session.commit()
    return JSONResponse({"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
                         "expires_in": 3600, "scope": " ".join(credential.scopes)}, headers=NO_STORE)


@router.post(PREFIX + "/token")
async def token(request: Request, session: AsyncSession = Depends(get_session)):
    enabled()
    p = await payload(request, form=True)
    if not p or p.get("resource", resource()) != resource():
        return error()
    if p.get("grant_type") == "authorization_code":
        row = (await session.execute(select(Authorization).where(
            Authorization.code_hash == token_hash(p.get("code", ""))).with_for_update())).scalar_one_or_none()
        verifier = p.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if (row is None or row.consumed_at or row.expires_at <= now() or row.client_id != p.get("client_id")
                or row.redirect_uri != p.get("redirect_uri") or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
                or not hmac.compare_digest(row.challenge, challenge)):
            return error("invalid_grant")
        user = await session.get(User, row.user_id)
        if user is None or not user.is_active or user.role not in {"analyst", "admin"}:
            return error("invalid_grant")
        row.consumed_at = now()
        credential = IntegrationToken(id=uuid.uuid4(), user_id=user.id, name="WorkBuddy 自动授权",
                                      scopes=row.scopes, token_hash=token_hash(secrets.token_urlsafe(32)),
                                      expires_at=now() + timedelta(hours=1))
        session.add(credential)
        await session.flush()
        grant = Grant(token_id=credential.id, client_id=row.client_id, expires_at=now() + timedelta(days=90))
        session.add(grant)
        await session.flush()
        session.add(OperationLog(user_id=user.id, action="workbuddy_oauth_connected", detail=json.dumps({"token_id": str(credential.id)})))
        return await issue(session, credential, grant)
    if p.get("grant_type") == "refresh_token":
        refresh = await session.get(Refresh, token_hash(p.get("refresh_token", "")))
        if refresh is None:
            return error("invalid_grant")
        # Serialize the entire grant; re-read refresh after locking to detect races/replay.
        grant = (await session.execute(select(Grant).where(Grant.token_id == refresh.grant_id).with_for_update())).scalar_one()
        await session.refresh(refresh)
        credential = await session.get(IntegrationToken, grant.token_id, with_for_update=True)
        if grant.client_id != p.get("client_id"):
            return error("invalid_grant")
        if refresh.used_at is not None:
            credential.revoked_at = now()
            await session.commit()
            return error("invalid_grant", "授权已失效，请重新连接。")
        user = await session.get(User, credential.user_id)
        if (credential.revoked_at or grant.expires_at <= now() or user is None or not user.is_active
                or user.role not in {"analyst", "admin"}):
            return error("invalid_grant")
        if "scope" in p and set(p["scope"].split()) != set(credential.scopes):
            return error("invalid_scope")
        refresh.used_at = now()
        return await issue(session, credential, grant)
    return error("unsupported_grant_type")


@router.post(PREFIX + "/revoke")
async def revoke(request: Request, session: AsyncSession = Depends(get_session)):
    p = await payload(request, form=True)
    if not p:
        return error()
    hashed = token_hash(p.get("token", ""))
    refresh = await session.get(Refresh, hashed)
    query = select(IntegrationToken, Grant).join(Grant, Grant.token_id == IntegrationToken.id).where(Grant.client_id == p.get("client_id"))
    query = query.where(IntegrationToken.id == refresh.grant_id) if refresh else query.where(IntegrationToken.token_hash == hashed)
    row = (await session.execute(query.with_for_update(of=Grant))).first()
    if row:
        credential = await session.get(IntegrationToken, row[0].id, with_for_update=True)
        credential.revoked_at = now()
        await session.commit()
    return JSONResponse({}, headers=NO_STORE)

"""Enterprise WeChat OAuth login for internal users."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode, quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


from ..auth import SECRET, TOKEN_LIFETIME, _password_helper, get_jwt_strategy
from ..db import get_session
from ..db.models import User
from ..utils.logger import log_operation
from ..utils.rate_limiter import login_rate_limiter, get_client_ip

router = APIRouter(prefix="/auth/wecom", tags=["auth"])

# PC browser: shows QR code to scan with WeCom app
WECOM_QR_URL = "https://open.work.weixin.qq.com/wwopen/sso/qrConnect"
# Mobile / WeCom in-app browser: redirects to or silently authorises via WeCom app
WECOM_OAUTH2_URL = "https://open.weixin.qq.com/connect/oauth2/authorize"
WECOM_GET_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_GET_USERINFO_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo"
WECOM_GET_USER_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/get"
# Returns sensitive fields (email, biz_mail) when user_ticket is present (snsapi_privateinfo flow)
WECOM_GET_USERDETAIL_URL = "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserdetail"
_STATE_TTL_SECONDS = 10 * 60

_access_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


class WeComExchangeRequest(BaseModel):
    code: str
    state: str


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _required_config() -> tuple[str, str, str]:
    corpid = _env("WECOM_CORP_ID")
    agentid = _env("WECOM_AGENT_ID")
    secret = _env("WECOM_APP_SECRET")
    missing = [
        name
        for name, value in (
            ("WECOM_CORP_ID", corpid),
            ("WECOM_AGENT_ID", agentid),
            ("WECOM_APP_SECRET", secret),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Enterprise WeChat login is not configured: {', '.join(missing)}",
        )
    return corpid, agentid, secret


def _sign_state(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{body}.{signature}"


def _decode_state(state: str) -> dict[str, Any]:
    try:
        body, signature = state.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid OAuth state") from exc

    ts = int(payload.get("ts", 0))
    if not ts or time.time() - ts > _STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="OAuth state expired")
    return payload


def _state() -> str:
    return _sign_state({"ts": int(time.time()), "nonce": secrets.token_urlsafe(16)})


def _synthetic_email(userid: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", userid).strip(".-_").lower()
    if not safe:
        safe = hashlib.sha256(userid.encode()).hexdigest()[:16]
    return f"wecom.{safe[:48]}@wecom.local"


def _default_role() -> str:
    role = os.getenv("WECOM_DEFAULT_ROLE", "viewer").strip().lower()
    return role if role in {"viewer", "analyst", "admin"} else "viewer"


async def _wecom_get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    timeout = float(os.getenv("WECOM_HTTP_TIMEOUT", "10"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errcode", 0) not in (0, "0"):
        message = payload.get("errmsg") or "Enterprise WeChat API error"
        raise HTTPException(status_code=502, detail=message)
    return payload


async def _wecom_post_json(url: str, access_token: str, body: dict[str, Any]) -> dict[str, Any]:
    timeout = float(os.getenv("WECOM_HTTP_TIMEOUT", "10"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, params={"access_token": access_token}, json=body)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errcode", 0) not in (0, "0"):
        message = payload.get("errmsg") or "Enterprise WeChat API error"
        raise HTTPException(status_code=502, detail=message)
    return payload


async def _get_access_token(corpid: str, secret: str) -> str:
    now = time.time()
    cached = _access_token_cache.get("token")
    if cached and float(_access_token_cache.get("expires_at", 0)) > now + 60:
        return str(cached)

    payload = await _wecom_get_json(
        WECOM_GET_TOKEN_URL,
        {"corpid": corpid, "corpsecret": secret},
    )
    token = payload.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="Enterprise WeChat did not return access_token")
    _access_token_cache["token"] = token
    _access_token_cache["expires_at"] = now + int(payload.get("expires_in", 7200))
    return str(token)


async def _fetch_wecom_identity(code: str) -> dict[str, Any]:
    corpid, _, secret = _required_config()
    access_token = await _get_access_token(corpid, secret)
    identity = await _wecom_get_json(
        WECOM_GET_USERINFO_URL,
        {"access_token": access_token, "code": code},
    )
    userid = identity.get("UserId") or identity.get("userid")
    if not userid:
        raise HTTPException(status_code=403, detail="Only Enterprise WeChat members can sign in")

    # user_ticket is only present when the OAuth scope is snsapi_privateinfo
    user_ticket = identity.get("user_ticket")

    # user/get provides name and biz_mail for the QR scan (snsapi_base / PC) flow.
    # Requires the WeCom app to have "获取成员信息" (Member Info Read) permission.
    profile: dict[str, Any] = {}
    try:
        profile = await _wecom_get_json(
            WECOM_GET_USER_URL,
            {"access_token": access_token, "userid": str(userid)},
        )
    except HTTPException as exc:
        import logging
        logging.getLogger(__name__).warning(
            "WeCom user/get failed for %s (check app Member Info permission): %s",
            userid, exc.detail,
        )

    name = profile.get("name") or str(userid)
    email: str | None = profile.get("biz_mail") or profile.get("email")

    # getuserdetail returns sensitive fields when user explicitly consented (snsapi_privateinfo)
    if user_ticket:
        try:
            detail = await _wecom_post_json(
                WECOM_GET_USERDETAIL_URL,
                access_token,
                {"user_ticket": user_ticket},
            )
            email = detail.get("biz_mail") or detail.get("email") or email
        except HTTPException:
            pass

    return {
        "userid": str(userid),
        "email": str(email or _synthetic_email(str(userid))).lower(),
        "name": name,
    }


async def _find_or_create_user(
    session: AsyncSession,
    identity: dict[str, Any],
) -> User:
    wecom_id = identity["userid"]
    email = identity["email"]
    name = identity.get("name") or ""

    # Primary lookup: wecom_userid (correct even if email changes)
    result = await session.execute(select(User).where(User.wecom_userid == wecom_id))
    user = result.scalar_one_or_none()

    if user is None:
        # Backwards compat: users created before this migration were matched by synthetic email
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            user.wecom_userid = wecom_id

    if user is not None:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User account is inactive")
        updated = False
        if name and user.display_name != name:
            user.display_name = name
            updated = True
        # Upgrade synthetic placeholder to real corporate email on next login.
        # If another account already owns that real email, transfer wecom_userid to it
        # and retire this synthetic-email ghost to avoid UniqueViolationError.
        if email and user.email.endswith("@wecom.local") and not email.endswith("@wecom.local"):
            collision = await session.execute(select(User).where(User.email == email))
            other = collision.scalar_one_or_none()
            if other is not None and other.id != user.id:
                # other.wecom_userid must be NULL or same id; don't overwrite a different binding
                if other.wecom_userid is None or other.wecom_userid == wecom_id:
                    # Clear wecom_userid on the ghost first and flush so the unique
                    # constraint is released before we assign it to the real account.
                    user.wecom_userid = None
                    user.is_active = False
                    await session.flush()
                    other.wecom_userid = wecom_id
                    if name and not other.display_name:
                        other.display_name = name
                    await session.commit()
                    return other
                # else: conflicting bindings — skip upgrade, keep synthetic email
            else:
                user.email = email
                updated = True
        if updated:
            await session.commit()
        return user

    auto_create = os.getenv("WECOM_AUTO_CREATE_USERS", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    if not auto_create:
        raise HTTPException(status_code=403, detail="Enterprise WeChat user is not allowed")

    count_result = await session.execute(select(func.count(User.id)))
    role = "admin" if count_result.scalar_one() == 0 else _default_role()
    helper = _password_helper()
    random_password = secrets.token_urlsafe(32)
    user = User(
        email=email,
        hashed_password=helper.hash(random_password),
        role=role,
        is_active=True,
        is_superuser=role == "admin",
        is_verified=True,
        wecom_userid=wecom_id,
        display_name=name or wecom_id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    await log_operation(
        str(user.id),
        "wecom_register",
        {"email": email, "wecom_userid": wecom_id, "name": name},
    )
    return user


def _allowed_redirect_origins() -> list[str]:
    """Return the set of allowed redirect URI prefixes from env config."""
    candidates = [
        os.getenv("WECOM_STREAMLIT_REDIRECT_URI"),
        os.getenv("APP_URL"),
        os.getenv("STREAMLIT_URL"),
    ]
    origins = [c.rstrip("/") for c in candidates if c and c.strip()]
    if not origins:
        origins = ["http://localhost:8501"]
    return origins


@router.get("/authorize-url")
async def authorize_url(redirect_uri: str) -> dict[str, Any]:
    allowed = _allowed_redirect_origins()
    if not any(redirect_uri.rstrip("/").startswith(origin) for origin in allowed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not in the allowed list",
        )
    corpid, agentid, _ = _required_config()
    state = _state()

    # PC browser: QR code flow
    qr_query = urlencode(
        {"appid": corpid, "agentid": agentid, "redirect_uri": redirect_uri, "state": state},
        quote_via=quote,
    )

    # Mobile / WeCom in-app browser: oauth2 flow with snsapi_privateinfo so that
    # getuserinfo returns a user_ticket, which is required by getuserdetail to
    # return email / biz_mail (WeCom API change effective June 2022).
    # The user will see a one-time consent popup on first login.
    oauth2_query = urlencode(
        {
            "appid": corpid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "snsapi_privateinfo",
            "state": state,
            "agentid": agentid,
        },
        quote_via=quote,
    )

    return {
        "enabled": True,
        "authorize_url": f"{WECOM_QR_URL}?{qr_query}",
        "oauth2_url": f"{WECOM_OAUTH2_URL}?{oauth2_query}#wechat_redirect",
        "expires_in": _STATE_TTL_SECONDS,
    }


@router.post("/exchange")
async def exchange(
    payload: WeComExchangeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ip = get_client_ip(request)
    await login_rate_limiter.check(ip, "wecom_exchange")

    try:
        _decode_state(payload.state)
        identity = await _fetch_wecom_identity(payload.code)
        user = await _find_or_create_user(session, identity)
    except HTTPException as exc:
        await login_rate_limiter.record_failure(ip, "wecom_exchange")
        raise

    await login_rate_limiter.reset(ip, "wecom_exchange")
    token = await get_jwt_strategy().write_token(user)
    await log_operation(str(user.id), "wecom_login", {"wecom_userid": identity["userid"]})
    display = user.display_name or identity.get("name") or user.email.split("@")[0]
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_LIFETIME,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "role": user.role,
            "name": display,
        },
    }

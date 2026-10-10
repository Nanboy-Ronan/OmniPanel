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
from urllib.parse import urlencode, quote, urlsplit, parse_qs

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
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
_STATE_COOKIE = "wecom_oauth_state"

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


PENDING_DETAIL = "账号尚未开通或已停用，请联系管理员在「用户管理」中启用。"


class SignInRefused(HTTPException):
    """A 403 that carries who was refused and why, for the audit trail."""

    def __init__(self, reason: str, detail: str, *, user_id=None, wecom_userid: str | None = None):
        super().__init__(status_code=403, detail=detail)
        self.reason = reason
        self.user_id = str(user_id) if user_id else None
        self.wecom_userid = wecom_userid


def _failure_reason(exc: HTTPException) -> str:
    if isinstance(exc, SignInRefused):
        return exc.reason
    if exc.status_code == 429:
        return "rate_limited"
    if exc.status_code == 502:
        return "wecom_unavailable"
    if exc.status_code == 400:
        return "invalid_state"
    if exc.status_code == 403:
        return "not_member"
    return f"http_{exc.status_code}"


async def _audit_failed_sign_in(request: Request, exc: HTTPException) -> None:
    """Record a refused WeCom sign-in; auditing must never mask the real error."""
    import logging

    refused = exc if isinstance(exc, SignInRefused) else None
    detail = {"reason": _failure_reason(exc)}
    if refused and refused.wecom_userid:
        detail["wecom_userid"] = refused.wecom_userid
    try:
        await log_operation(
            refused.user_id if refused else None, "wecom_login_failed", detail, request=request,
        )
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("could not audit failed sign-in", exc_info=True)


def _new_users_active() -> bool:
    return os.getenv("WECOM_NEW_USERS_ACTIVE", "false").strip().lower() in ("1", "true", "yes")


async def _notify_pending_user(name: str, email: str) -> None:
    """Tell administrators someone is waiting; never block login on delivery."""
    import asyncio
    import logging

    from ..utils.wecom_bot import send_wecom_alert

    try:
        await asyncio.to_thread(
            send_wecom_alert,
            f"[访问申请] {name}（{email}）通过企业微信登录了数据工作台，"
            "账号待开通。请在「用户管理」中确认角色后启用。",
        )
    except Exception:  # noqa: BLE001 - notification is best effort
        logging.getLogger(__name__).warning("pending-user notification failed", exc_info=True)


def _default_role() -> str:
    role = os.getenv("WECOM_DEFAULT_ROLE", "viewer").strip().lower()
    return role if role in {"viewer", "analyst", "admin"} else "viewer"


@router.get("/status", include_in_schema=False)
async def login_status() -> dict[str, bool]:
    return {"enabled": all((_env("WECOM_CORP_ID"), _env("WECOM_AGENT_ID"), _env("WECOM_APP_SECRET")))}


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
            raise SignInRefused("inactive", PENDING_DETAIL, user_id=user.id, wecom_userid=wecom_id)
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
        raise SignInRefused(
            "auto_create_disabled", "Enterprise WeChat user is not allowed", wecom_userid=wecom_id,
        )

    count_result = await session.execute(select(func.count(User.id)))
    first_user = count_result.scalar_one() == 0
    role = "admin" if first_user else _default_role()
    # Any member of the enterprise can complete WeCom OAuth, so a new account
    # waits for an administrator unless the deployment opts into open access.
    active = first_user or _new_users_active()
    helper = _password_helper()
    random_password = secrets.token_urlsafe(32)
    user = User(
        email=email,
        hashed_password=helper.hash(random_password),
        role=role,
        is_active=active,
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
        {"email": email, "wecom_userid": wecom_id, "name": name, "active": active},
    )
    if not active:
        await _notify_pending_user(name or wecom_id, email)
        raise SignInRefused("pending_approval", PENDING_DETAIL, user_id=user.id, wecom_userid=wecom_id)
    return user


def _allowed_redirect_origins() -> list[str]:
    """Return configured callback URLs (never interpret them as prefixes)."""
    candidates = [
        os.getenv("WECOM_CONSOLE_REDIRECT_URI"),
        os.getenv("WECOM_STREAMLIT_REDIRECT_URI"),
        os.getenv("APP_URL"),
        os.getenv("STREAMLIT_URL"),
    ]
    origins = [c.rstrip("/") for c in candidates if c and c.strip()]
    if not origins:
        origins = ["http://localhost:5173/console"]
    return origins


def _callback_key(value: str) -> tuple | None:
    """Compare callback destinations without accepting lookalike hosts or paths."""
    try:
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
            return None
        uri = urlsplit(value)
        if (uri.scheme not in {"http", "https"} or not uri.hostname
                or uri.username is not None or uri.password is not None
                or uri.query or uri.fragment):
            return None
        port = uri.port if uri.port is not None else (443 if uri.scheme == "https" else 80)
        return (uri.scheme, uri.hostname, port,
                uri.path.rstrip("/"))
    except ValueError:
        return None


@router.get("/authorize-url")
async def authorize_url(redirect_uri: str, response: Response) -> dict[str, Any]:
    allowed = _allowed_redirect_origins()
    callback = _callback_key(redirect_uri)
    if callback is None or callback not in {_callback_key(origin) for origin in allowed}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not in the allowed list",
        )
    corpid, agentid, _ = _required_config()
    state = _state()
    # Legacy API clients keep this cookie in their HTTP session.
    # A signed state alone does not prove that this browser initiated the login.
    response.set_cookie(
        _STATE_COOKIE, state, max_age=_STATE_TTL_SECONDS,
        httponly=True, samesite="lax", path="/auth/wecom",
    )

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


@router.get("/start", include_in_schema=False)
async def start_browser_login(
    redirect_uri: str, flow: str = "qr",
    return_query: str = Query("", max_length=500),
) -> RedirectResponse:
    """Start OAuth with a browser-owned state cookie for the same-origin console."""
    if flow not in {"qr", "mobile"}:
        raise HTTPException(status_code=400, detail="Invalid login flow")
    payload = await authorize_url(redirect_uri, Response())
    target = payload["oauth2_url"] if flow == "mobile" else payload["authorize_url"]
    state = parse_qs(urlsplit(target).query)["state"][0]
    redirect = RedirectResponse(target, status_code=302)
    redirect.set_cookie(
        _STATE_COOKIE, state, max_age=_STATE_TTL_SECONDS,
        httponly=True, samesite="lax", secure=urlsplit(redirect_uri).scheme == "https",
        path="/",
    )
    if return_query:
        encoded_return = base64.urlsafe_b64encode(return_query.encode()).decode().rstrip("=")
        redirect.set_cookie(
            "dashboard_login_return", encoded_return, max_age=_STATE_TTL_SECONDS,
            httponly=True, samesite="lax", secure=urlsplit(redirect_uri).scheme == "https",
            path="/",
        )
    redirect.headers["Cache-Control"] = "no-store"
    return redirect


@router.post("/exchange")
async def exchange(
    payload: WeComExchangeRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ip = get_client_ip(request)
    try:
        await login_rate_limiter.check(ip, "wecom_exchange")
    except HTTPException as exc:
        await _audit_failed_sign_in(request, exc)
        raise

    try:
        expected_state = request.cookies.get(_STATE_COOKIE, "")
        if not expected_state or not hmac.compare_digest(payload.state, expected_state):
            raise HTTPException(status_code=400, detail="OAuth state does not match this login session")
        _decode_state(payload.state)
        identity = await _fetch_wecom_identity(payload.code)
        user = await _find_or_create_user(session, identity)
    except HTTPException as exc:
        await login_rate_limiter.record_failure(ip, "wecom_exchange")
        await _audit_failed_sign_in(request, exc)
        raise

    await login_rate_limiter.reset(ip, "wecom_exchange")
    # /authorize-url scoped the cookie to this route, while browser /start
    # scoped it to /. Clear both so a completed login cannot retain stale state.
    response.delete_cookie(_STATE_COOKIE, path="/auth/wecom")
    response.delete_cookie(_STATE_COOKIE, path="/")
    token = await get_jwt_strategy().write_token(user)
    await log_operation(
        str(user.id), "wecom_login", {"wecom_userid": identity["userid"]}, request=request,
    )
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

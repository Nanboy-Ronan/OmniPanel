"""Optional WeCom (企业微信) alerting via the existing self-built app.

Used by the creator-portal collector (app/collector/) to notify on failure —
session expiry, download timeout, upload failure. Reuses the same
WECOM_CORP_ID / WECOM_AGENT_ID / WECOM_APP_SECRET already configured for
Enterprise WeChat OAuth login (see app/views/wecom_auth.py) — no separate
credential to manage.

A group-bot webhook (企业微信自定义群机器人) was the original design, but
custom webhook bots require a pure-internal group and can be disabled by an
enterprise admin; this org has that feature restricted with no admin access
to re-enable it. Sending an app message (企业微信自建应用发送消息) sidesteps
that entirely — any self-built app can message its visible users without
needing group-robot permissions.

Left unconfigured (any of the three env vars missing), alerts are silently
skipped and nothing else in the app is affected.

Recipients: WECOM_ALERT_TOUSER (env), if set, is an explicit ops override and
always wins. Otherwise falls back to the admin-togglable per-user opt-in in
the 用户管理 UI (User.wecom_alert_enabled, requires a linked wecom_userid),
and finally to "@all" if nobody has opted in (e.g. a fresh deploy before any
admin has visited the UI).
"""
from __future__ import annotations

import logging
import os

import httpx

_logger = logging.getLogger(__name__)

_GET_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
_SEND_MESSAGE_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _resolve_touser() -> str:
    """Who receives alerts: WECOM_ALERT_TOUSER (explicit ops override) first,
    then the DB opt-in list (用户管理 UI), then "@all"."""
    override = _env("WECOM_ALERT_TOUSER")
    if override:
        return override
    try:
        from ..db import SyncSessionLocal
        from ..db.models import User
        with SyncSessionLocal() as session:
            ids = [
                row[0] for row in session.query(User.wecom_userid).filter(
                    User.wecom_alert_enabled.is_(True), User.wecom_userid.isnot(None)
                ).all()
            ]
        if ids:
            return "|".join(ids)
    except Exception:
        _logger.warning("wecom_alert_touser_db_lookup_failed", exc_info=True)
    return "@all"


def send_wecom_alert(text: str) -> bool:
    """Send a text message via the WeCom self-built app.

    Recipient is resolved by _resolve_touser() — see module docstring.

    Returns True if the message was accepted, False otherwise (including
    when WeCom alerting isn't configured). Never raises — callers use this
    from error-handling paths and a broken alert must not mask the original
    error that triggered it.
    """
    corpid = _env("WECOM_CORP_ID")
    agentid = _env("WECOM_AGENT_ID")
    secret = _env("WECOM_APP_SECRET")
    if not (corpid and agentid and secret):
        return False
    touser = _resolve_touser()

    try:
        token_resp = httpx.get(
            _GET_TOKEN_URL,
            params={"corpid": corpid, "corpsecret": secret},
            timeout=10,
        )
        token_resp.raise_for_status()
        token_body = token_resp.json()
        access_token = token_body.get("access_token")
        if not access_token:
            _logger.warning("wecom_alert_no_token body=%r", token_body)
            return False

        send_resp = httpx.post(
            _SEND_MESSAGE_URL,
            params={"access_token": access_token},
            json={
                "touser": touser,
                "msgtype": "text",
                "agentid": int(agentid),
                "text": {"content": text},
            },
            timeout=10,
        )
        if send_resp.status_code != 200:
            _logger.warning("wecom_alert_failed status=%d body=%r", send_resp.status_code, send_resp.text)
            return False
        send_body = send_resp.json()
        if send_body.get("errcode", 0) != 0:
            _logger.warning("wecom_alert_rejected body=%r", send_body)
            return False
        return True
    except Exception as exc:
        _logger.warning("wecom_alert_error: %s", exc)
        return False

"""Explicit-recipient delivery with an honest unknown state after network ambiguity."""
import re

import httpx
from sqlalchemy import select

from ..db.models import User
from ..utils.wecom_bot import _env, _GET_TOKEN_URL, _SEND_MESSAGE_URL, truncate_utf8


async def configured_recipients(session) -> list[str]:
    """Reuse the existing notification override and 用户管理 opt-in settings."""
    override = _env("WECOM_ALERT_TOUSER")
    if override and override != "@all":
        ids = list(dict.fromkeys(value.strip() for value in override.split("|") if value.strip()))
    else:
        query = select(User.wecom_userid).where(
            User.is_active.is_(True), User.role.in_(["analyst", "admin"]), User.wecom_userid.isnot(None),
        )
        if override != "@all":
            query = query.where(User.wecom_alert_enabled.is_(True))
        ids = sorted(set((await session.execute(query)).scalars().all()))
    if not ids or len(ids) > 100 or any(not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", i) or i.startswith("@") for i in ids):
        raise ValueError("未找到有效周报收件人，请检查用户管理中已绑定企微的分析员/管理员通知设置。")
    return ids


async def validate_recipients(session, recipients: list[str]) -> None:
    allowed = set((await session.execute(select(User.wecom_userid).where(
        User.wecom_userid.in_(recipients), User.is_active.is_(True), User.role.in_(["analyst", "admin"]),
    ))).scalars().all())
    current = set(await configured_recipients(session))
    if not recipients or set(recipients) != allowed or not set(recipients) <= current:
        raise ValueError("周报收件人必须全部是已绑定企微、已激活的分析员或管理员。")


def send_report(message: str, recipients: list[str]) -> str:
    """Return sent / failed / unknown. Never infer success from a timeout."""
    if not recipients or any(i.startswith("@") for i in recipients):
        return "failed"
    corpid, agentid, secret = (_env(key) for key in ("WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_APP_SECRET"))
    if not all((corpid, agentid, secret)):
        return "failed"
    try:
        token_response = httpx.get(_GET_TOKEN_URL, params={"corpid": corpid, "corpsecret": secret}, timeout=10)
        token_response.raise_for_status()
        token = token_response.json().get("access_token")
        if not token:
            return "failed"
        payload = {"touser": "|".join(recipients), "agentid": int(agentid), "msgtype": "text", "text": {"content": truncate_utf8(message)}}
    except Exception:
        return "failed"  # No send request has happened.
    try:
        response = httpx.post(_SEND_MESSAGE_URL, params={"access_token": token}, json=payload, timeout=10)
        if response.status_code != 200:
            return "unknown"
        body = response.json()
        if body.get("errcode") == 0:
            if any(body.get(key) for key in ("invaliduser", "invalidparty", "invalidtag", "unlicenseduser")):
                return "unknown"  # A partial success may have reached some recipients.
            return "sent"
        return "failed" if "errcode" in body else "unknown"
    except Exception:
        return "unknown"

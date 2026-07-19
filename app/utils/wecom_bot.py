"""Optional WeCom (企业微信) group-bot webhook alerting.

Used by the creator-portal collector (app/collector/) to notify a group chat
on failure — session expiry, download timeout, upload failure. Configure
``WECOM_BOT_WEBHOOK`` to enable; left unset, alerts are silently skipped and
nothing else in the app is affected.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

_logger = logging.getLogger(__name__)


def send_wecom_alert(text: str) -> bool:
    """POST a text message to the configured WeCom group-bot webhook.

    Returns True if the message was accepted, False otherwise (including
    when no webhook is configured). Never raises — callers use this from
    error-handling paths and a broken alert must not mask the original error.
    """
    webhook = settings.wecom_bot_webhook
    if not webhook:
        return False
    try:
        resp = httpx.post(
            webhook,
            json={"msgtype": "text", "text": {"content": text}},
            timeout=10,
        )
        if resp.status_code != 200:
            _logger.warning("wecom_alert_failed status=%d body=%r", resp.status_code, resp.text)
            return False
        body = resp.json()
        if body.get("errcode", 0) != 0:
            _logger.warning("wecom_alert_rejected body=%r", body)
            return False
        return True
    except Exception as exc:
        _logger.warning("wecom_alert_error: %s", exc)
        return False

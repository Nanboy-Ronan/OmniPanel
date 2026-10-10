import json
import logging
import uuid as _uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

from .. import db as _db_mod


def log_exc(logger: logging.Logger, msg: str, exc: Exception, **ctx) -> None:
    """Log an exception at ERROR level with structured key=value context.

    Always attaches exc_info so the stack trace appears in log output.
    """
    if ctx:
        ctx_str = " ".join(f"{k}={v!r}" for k, v in sorted(ctx.items()))
        full_msg = f"{msg} {ctx_str}"
    else:
        full_msg = msg
    logger.error(full_msg, exc_info=exc)


def request_context(request: "Request | None") -> dict:
    """Client IP and user agent for the audit trail (IP already proxy-resolved)."""
    if request is None:
        return {}
    from .rate_limiter import get_client_ip

    agent = request.headers.get("user-agent", "")
    return {"ip": get_client_ip(request), "user_agent": agent[:200]} if agent else {"ip": get_client_ip(request)}


async def log_operation(
    user_id: str | None,
    action: str,
    detail: dict | None = None,
    *,
    session: "AsyncSession | None" = None,
    request: "Request | None" = None,
) -> None:
    """Insert a new OperationLog row with optional detail.

    Pass ``session`` to reuse an existing connection instead of opening a new
    pool connection. The session must have already committed its main
    transaction; log_operation issues its own commit on the same connection.
    Pass ``request`` to record the client IP and user agent. ``user_id`` may be
    None for sign-in attempts that match no account.
    """
    from ..db.models import OperationLog
    merged = {**(detail or {}), **request_context(request)}
    detail_str = json.dumps(merged, ensure_ascii=False, default=str) if merged else None
    if user_id is None:
        uid = None
    else:
        try:
            uid = _uuid.UUID(user_id)
        except (ValueError, TypeError):
            uid = user_id
    entry = OperationLog(user_id=uid, action=action, detail=detail_str)
    if session is not None:
        session.add(entry)
        await session.commit()
    else:
        async with _db_mod.AsyncSessionLocal() as s:
            s.add(entry)
            await s.commit()

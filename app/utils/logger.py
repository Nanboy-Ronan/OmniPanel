import json
import logging
import uuid as _uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


async def log_operation(
    user_id: str,
    action: str,
    detail: dict | None = None,
    *,
    session: "AsyncSession | None" = None,
) -> None:
    """Insert a new OperationLog row with optional detail.

    Pass ``session`` to reuse an existing connection instead of opening a new
    pool connection. The session must have already committed its main
    transaction; log_operation issues its own commit on the same connection.
    """
    from ..db.models import OperationLog
    detail_str = json.dumps(detail, ensure_ascii=False) if detail else None
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

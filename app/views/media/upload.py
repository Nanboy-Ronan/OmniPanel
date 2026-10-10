"""app/views/media/upload.py

POST /media/accounts — admin creates a media account (no app credentials required)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_admin_user
from ...db import get_session
from ...db.models import MediaAccount
from ...utils.logger import log_operation

upload_router = APIRouter(prefix="/media", tags=["media"])

WECHAT_PLATFORM = "wechat_official"


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CreateAccountRequest(BaseModel):
    name: str
    app_id: str | None = None


# ── Routes ─────────────────────────────────────────────────────────────────────

@upload_router.post("/accounts", status_code=201)
async def create_media_account(
    request: Request,
    payload: CreateAccountRequest,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Admin creates a media account (no WeChat API credentials required)."""
    account = MediaAccount(
        platform=WECHAT_PLATFORM,
        name=payload.name,
        app_id=payload.app_id,
        is_active=True,
    )
    session.add(account)
    await session.commit()
    await log_operation(str(_u.id), "media_account_create", {"account_id": account.id, "name": account.name}, request=request)
    await session.refresh(account)
    return {
        "id":        account.id,
        "platform":  account.platform,
        "name":      account.name,
        "app_id":    account.app_id,
        "is_active": bool(account.is_active),
    }

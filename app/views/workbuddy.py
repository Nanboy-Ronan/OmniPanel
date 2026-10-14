"""Self-service token lifecycle; only a logged-in analyst can manage their tokens."""
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_analyst_user
from ..config import settings
from ..db import get_session
from ..db.models import IntegrationToken, OperationLog, WorkBuddyOAuthGrant
from ..integrations.workbuddy.auth import token_hash

router = APIRouter(prefix="/integrations/workbuddy", tags=["workbuddy"])
Scope = Literal["metadata:read", "analytics:ecommerce", "analytics:media", "reports:read"]


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    expires_in_days: int = Field(default=30, ge=1, le=90)
    scopes: list[Scope] = Field(min_length=1, max_length=4)


class TokenSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    authorization_expires_at: datetime | None = None


class CreatedToken(TokenSummary):
    token: str


@router.get("/status")
async def integration_status(_user=Depends(current_analyst_user), session: AsyncSession = Depends(get_session)):
    from ..integrations.workbuddy.reports import configured
    from ..reports.workbuddy_delivery import configured_recipients, validate_recipients
    try:
        recipients = await configured_recipients(session)
        await validate_recipients(session, recipients)
        recipients_ready = True
    except ValueError:
        recipients_ready = False
    return {"mcp_enabled": settings.workbuddy_mcp_enabled,
            "report_api_ready": configured() and recipients_ready and settings.report_provider == "workbuddy"}


@router.get("/tokens", response_model=list[TokenSummary])
async def list_tokens(response: Response, user=Depends(current_analyst_user), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    rows = (await session.execute(
        select(IntegrationToken, WorkBuddyOAuthGrant.expires_at)
        .outerjoin(WorkBuddyOAuthGrant, WorkBuddyOAuthGrant.token_id == IntegrationToken.id)
        .where(IntegrationToken.user_id == user.id).order_by(IntegrationToken.created_at.desc())
    )).all()
    return [TokenSummary.model_validate(row).model_copy(update={"authorization_expires_at": expiry}) for row, expiry in rows]


@router.post("/tokens", response_model=CreatedToken, status_code=201)
async def create_token(body: TokenCreate, response: Response, user=Depends(current_analyst_user), session: AsyncSession = Depends(get_session)):
    if not settings.workbuddy_mcp_enabled:
        raise HTTPException(503, "WorkBuddy 接入尚未启用，请联系管理员。")
    raw = "rpa_wb_" + secrets.token_urlsafe(32)
    credential = IntegrationToken(
        id=uuid.uuid4(), user_id=user.id, name=body.name.strip(), token_hash=token_hash(raw),
        scopes=sorted(set(body.scopes)), expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days),
    )
    session.add(credential)
    session.add(OperationLog(user_id=user.id, action="workbuddy_token_created", detail=json.dumps({"token_id": str(credential.id), "scopes": credential.scopes})))
    await session.commit()
    await session.refresh(credential)
    response.headers["Cache-Control"] = "no-store"
    return CreatedToken(**TokenSummary.model_validate(credential).model_dump(), token=raw)


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(token_id: uuid.UUID, user=Depends(current_analyst_user), session: AsyncSession = Depends(get_session)):
    credential = (await session.execute(select(IntegrationToken).where(
        IntegrationToken.id == token_id, IntegrationToken.user_id == user.id,
    ))).scalar_one_or_none()
    if credential is None:
        raise HTTPException(404, "访问令牌不存在")
    if credential.revoked_at is None:
        credential.revoked_at = datetime.now(timezone.utc)
        session.add(OperationLog(user_id=user.id, action="workbuddy_token_revoked", detail=json.dumps({"token_id": str(token_id)})))
        await session.commit()


@router.get("/queries")
async def query_history(response: Response, user=Depends(current_analyst_user), session: AsyncSession = Depends(get_session)):
    """Each employee can review only their own latest connector queries."""
    response.headers["Cache-Control"] = "no-store"
    rows = (await session.execute(select(OperationLog).where(
        OperationLog.user_id == user.id, OperationLog.action == "workbuddy_query"
    ).order_by(OperationLog.id.desc()).limit(30))).scalars()
    result = []
    for row in rows:
        detail = json.loads(row.detail or "{}")
        result.append({"timestamp": row.timestamp, **{key: detail.get(key) for key in (
            "request_id", "tool", "parameters", "outcome", "duration_ms", "returned_rows", "required_scope"
        )}})
    return result

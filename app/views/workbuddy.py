"""Self-service token lifecycle; only a logged-in analyst can manage their tokens."""
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_admin_user, current_analyst_user
from ..config import settings
from ..db import get_session
from ..db.models import IntegrationToken, OperationLog, User, WorkBuddyOAuthGrant
from ..integrations.workbuddy.auth import token_hash
from ..utils.logger import request_context

router = APIRouter(prefix="/integrations/workbuddy", tags=["workbuddy"])
admin_router = APIRouter(prefix="/admin/integrations/workbuddy", tags=["workbuddy"])


def audit(session, request, user_id, action, **detail):
    session.add(OperationLog(user_id=user_id, action=action,
                             detail=json.dumps({**detail, **request_context(request)}, ensure_ascii=False, default=str)))
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
async def create_token(body: TokenCreate, request: Request, response: Response, user=Depends(current_analyst_user), session: AsyncSession = Depends(get_session)):
    if not settings.workbuddy_mcp_enabled:
        raise HTTPException(503, "WorkBuddy 接入尚未启用，请联系管理员。")
    raw = "rpa_wb_" + secrets.token_urlsafe(32)
    credential = IntegrationToken(
        id=uuid.uuid4(), user_id=user.id, name=body.name.strip(), token_hash=token_hash(raw),
        scopes=sorted(set(body.scopes)), expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days),
    )
    session.add(credential)
    audit(session, request, user.id, "workbuddy_token_created", token_id=str(credential.id), scopes=credential.scopes)
    await session.commit()
    await session.refresh(credential)
    response.headers["Cache-Control"] = "no-store"
    return CreatedToken(**TokenSummary.model_validate(credential).model_dump(), token=raw)


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(token_id: uuid.UUID, request: Request, user=Depends(current_analyst_user), session: AsyncSession = Depends(get_session)):
    credential = (await session.execute(select(IntegrationToken).where(
        IntegrationToken.id == token_id, IntegrationToken.user_id == user.id,
    ))).scalar_one_or_none()
    if credential is None:
        raise HTTPException(404, "访问令牌不存在")
    if credential.revoked_at is None:
        credential.revoked_at = datetime.now(timezone.utc)
        audit(session, request, user.id, "workbuddy_token_revoked", token_id=str(token_id))
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


class AdminTokenSummary(TokenSummary):
    user_id: uuid.UUID
    user_email: str
    user_name: str | None = None
    kind: Literal["oauth", "token"]


@admin_router.get("/tokens", response_model=list[AdminTokenSummary])
async def list_all_tokens(response: Response, include_inactive: bool = False,
                          _admin=Depends(current_admin_user), session: AsyncSession = Depends(get_session)):
    """Every employee's connections, so a leaked one can be revoked without disabling the person."""
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(timezone.utc)
    stmt = (select(IntegrationToken, WorkBuddyOAuthGrant.expires_at, User.email, User.display_name)
            .join(User, User.id == IntegrationToken.user_id)
            .outerjoin(WorkBuddyOAuthGrant, WorkBuddyOAuthGrant.token_id == IntegrationToken.id)
            .order_by(IntegrationToken.created_at.desc()).limit(500))
    if not include_inactive:
        stmt = stmt.where(IntegrationToken.revoked_at.is_(None)).where(
            (IntegrationToken.expires_at > now) | (WorkBuddyOAuthGrant.expires_at > now))
    rows = (await session.execute(stmt)).all()
    return [AdminTokenSummary(**TokenSummary.model_validate(row).model_dump(exclude={"authorization_expires_at"}),
                              authorization_expires_at=grant_expiry, user_id=row.user_id, user_email=email,
                              user_name=name, kind="oauth" if grant_expiry else "token")
            for row, grant_expiry, email, name in rows]


@admin_router.delete("/tokens/{token_id}", status_code=204)
async def admin_revoke_token(token_id: uuid.UUID, request: Request, admin=Depends(current_admin_user),
                             session: AsyncSession = Depends(get_session)):
    credential = await session.get(IntegrationToken, token_id, with_for_update=True)
    if credential is None:
        raise HTTPException(404, "访问令牌不存在")
    if credential.revoked_at is None:
        credential.revoked_at = datetime.now(timezone.utc)
        audit(session, request, admin.id, "workbuddy_token_admin_revoked",
              token_id=str(token_id), target_user=str(credential.user_id))
        await session.commit()

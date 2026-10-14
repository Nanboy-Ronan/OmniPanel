"""Human-approved local browser login. No collector secrets enter MCP responses."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user, current_admin_user
from ..config import settings
from ..db import get_session
from ..db.models import CollectorOwner, CollectorRepair, CollectorRun, OperationLog, User, XhsAccount, WxChannelsAccount
from ..collector.paths import session_path
from .workbuddy_oauth import throttle

async def log_operation(user_id, action, detail, *, session):
    # Keep audit, grant changes and row locks in the caller's transaction.
    session.add(OperationLog(user_id=UUID(user_id), action=action, detail=json.dumps(detail)))


router = APIRouter(prefix="/collector/repair", tags=["collector-repair"])
Platform = Literal["xhs", "pugongying", "channels"]
LABELS = {"xhs": "小红书", "pugongying": "蒲公英", "channels": "视频号"}


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def target(session, platform, account_id):
    if platform not in LABELS or not account_id or account_id < 1:
        raise HTTPException(422, "不支持的采集账号")
    row = await session.get(WxChannelsAccount if platform == "channels" else XhsAccount, account_id)
    if row is None or not row.is_active or (platform == "pugongying" and not row.pgy_enabled):
        raise HTTPException(404, "采集账号未启用")
    return row


async def allowed(session, user, platform, account_id):
    if user is None or not user.is_active:
        return False
    if user.role == "admin":
        return True
    owner = await session.get(CollectorOwner, (platform, account_id))
    return owner is not None and owner.user_id == user.id


async def targets_for_user(session, user, *, include_all=False):
    results = []
    for platform, model in (("xhs", XhsAccount), ("pugongying", XhsAccount), ("channels", WxChannelsAccount)):
        rows = (await session.execute(select(model).where(model.is_active.is_(True)))).scalars()
        for account in rows:
            if platform == "pugongying" and not account.pgy_enabled:
                continue
            can_repair = await allowed(session, user, platform, account.id)
            if not can_repair and not include_all:
                continue
            owner = await session.get(CollectorOwner, (platform, account.id))
            run = (await session.execute(select(CollectorRun).where(
                CollectorRun.platform == platform, CollectorRun.account_id == account.id,
                CollectorRun.triggered_by == "verify").order_by(CollectorRun.started_at.desc()).limit(1))).scalar_one_or_none()
            results.append({"platform": platform, "account_id": account.id,
                "name": account.name, "label": f"{LABELS[platform]} · {account.name}",
                "owner_id": str(owner.user_id) if owner else None, "can_repair": can_repair,
                "verify_status": run.status if run else None,
                "login_missing": not session_path(platform, account.id).exists()})
    return results


@router.get("/targets")
async def list_targets(user=Depends(current_active_user), session: AsyncSession = Depends(get_session)):
    return await targets_for_user(session, user)


class OwnerUpdate(BaseModel):
    platform: Platform
    account_id: int = Field(ge=1)
    user_id: UUID | None


@router.put("/owner")
async def set_owner(body: OwnerUpdate, user=Depends(current_admin_user), session: AsyncSession = Depends(get_session)):
    await target(session, body.platform, body.account_id)
    if body.user_id:
        employee = await session.get(User, body.user_id)
        if employee is None or not employee.is_active:
            raise HTTPException(422, "负责人必须是已启用的平台用户")
    # Serialise owner changes for this target with final credential installation.
    await session.execute(select(XhsAccount if body.platform != "channels" else WxChannelsAccount).where(
        (XhsAccount if body.platform != "channels" else WxChannelsAccount).id == body.account_id).with_for_update())
    row = await session.get(CollectorOwner, (body.platform, body.account_id))
    if body.user_id:
        if row:
            row.user_id = body.user_id
        else:
            session.add(CollectorOwner(platform=body.platform, account_id=body.account_id, user_id=body.user_id))
    elif row:
        await session.delete(row)
    await log_operation(str(user.id), "collector_owner_update", {
        "platform": body.platform, "account_id": body.account_id, "owner_id": str(body.user_id) if body.user_id else None}, session=session)
    await session.commit()
    return {"status": "saved"}


@router.post("/pair")
async def pair(request: Request, session: AsyncSession = Depends(get_session)):
    await throttle(request, "repair_pair")
    # Expired records contain no cookie data and can be removed on the next pairing.
    await session.execute(delete(CollectorRepair).where(CollectorRepair.expires_at < now()))
    identifier, secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    session.add(CollectorRepair(id=identifier, secret_hash=digest(secret), expires_at=now() + timedelta(minutes=15), status="pending"))
    await session.commit()
    return JSONResponse({"id": identifier, "secret": secret,
        "verification_url": settings.public_origin + "/console/?repair=" + identifier},
        headers={"Cache-Control": "no-store"})


async def get_job(session, identifier, *, lock=False):
    stmt = select(CollectorRepair).where(CollectorRepair.id == identifier)
    row = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if row is None or row.expires_at <= now():
        raise HTTPException(410, "修复请求已过期，请从 WorkBuddy 重新发起")
    return row


@router.get("/requests/{identifier}")
async def inspect_request(identifier: str, user=Depends(current_active_user), session: AsyncSession = Depends(get_session)):
    row = await get_job(session, identifier)
    if row.user_id is not None and row.user_id != user.id:
        raise HTTPException(403)
    return {"status": row.status, "targets": await targets_for_user(session, user)}


class Approval(BaseModel):
    platform: Platform
    account_id: int = Field(ge=1)


@router.post("/requests/{identifier}")
async def approve(identifier: str, body: Approval, user=Depends(current_active_user), session: AsyncSession = Depends(get_session)):
    row = await get_job(session, identifier, lock=True)
    if row.status != "pending":
        raise HTTPException(409, "请求已经处理")
    await target(session, body.platform, body.account_id)
    if not await allowed(session, user, body.platform, body.account_id):
        raise HTTPException(403)
    row.user_id, row.platform, row.account_id, row.status = user.id, body.platform, body.account_id, "approved"
    await log_operation(str(user.id), "collector_repair_approve", {"repair_id": identifier, **body.model_dump()}, session=session)
    await session.commit()
    return {"status": "approved"}


async def helper_job(session, identifier, authorization, *, lock=False):
    row = await get_job(session, identifier, lock=lock)
    secret = (authorization or "").removeprefix("Bearer ")
    if not secrets.compare_digest(row.secret_hash, digest(secret)):
        raise HTTPException(401)
    return row


@router.get("/local/{identifier}")
async def poll(identifier: str, authorization: str | None = Header(None), session: AsyncSession = Depends(get_session)):
    row = await helper_job(session, identifier, authorization)
    result = {"status": row.status}
    if row.user_id:
        user = await session.get(User, row.user_id)
        if not await allowed(session, user, row.platform, row.account_id):
            raise HTTPException(403)
        account = await target(session, row.platform, row.account_id)
        result.update(platform=row.platform, account_id=row.account_id, name=account.name)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def validate_state(raw, platform):
    if len(raw) > 1024 * 1024:
        raise HTTPException(413)
    try:
        state = json.loads(raw)
        domains = ("xiaohongshu.com",) if platform in ("xhs", "pugongying") else ("weixin.qq.com", "qq.com")
        def valid_host(host):
            host = host.lstrip(".").lower()
            return any(host == d or host.endswith("." + d) for d in domains)
        if not isinstance(state, dict) or not state.get("cookies") or not isinstance(state["cookies"], list):
            raise ValueError()
        for cookie in state["cookies"]:
            if not isinstance(cookie, dict) or not valid_host(cookie["domain"]) or not isinstance(cookie["value"], str):
                raise ValueError()
        from urllib.parse import urlsplit
        for origin in state.get("origins", []):
            url = urlsplit(origin["origin"])
            if url.scheme != "https" or not valid_host(url.hostname or ""):
                raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, "登录凭证格式或来源不正确") from None
    return state


@router.post("/local/{identifier}")
async def upload(identifier: str, request: Request, authorization: str | None = Header(None), session: AsyncSession = Depends(get_session)):
    row = await helper_job(session, identifier, authorization, lock=True)
    if row.status != "approved":
        raise HTTPException(409, "该请求尚未授权或已使用")
    user = await session.get(User, row.user_id)
    if not await allowed(session, user, row.platform, row.account_id):
        raise HTTPException(403)
    account = await target(session, row.platform, row.account_id)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1024 * 1024:
            raise HTTPException(413)
    validate_state(raw, row.platform)
    row.status = "verifying"
    platform, account_id, name, user_id = row.platform, row.account_id, account.name, row.user_id
    await session.commit()  # One upload only; crashes require a fresh repair request.
    target_path = session_path(platform, account_id)
    fd, candidate = tempfile.mkstemp(prefix=".repair-", suffix=".json", dir=target_path.parent)
    candidate = Path(candidate)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        from ..collector.repair import verify_candidate
        try:
            valid = await asyncio.to_thread(verify_candidate, platform, candidate, name)
        except Exception:
            valid = False  # Never return portal content, cookies or browser exception details.
        # Lock account and user while rechecking revocation and installing.
        model = WxChannelsAccount if platform == "channels" else XhsAccount
        await session.execute(select(model).where(model.id == account_id).with_for_update())
        session.expire_all()
        user = (await session.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        row = await get_job(session, identifier, lock=True)
        account = await target(session, platform, account_id)
        if not await allowed(session, user, platform, account_id) or account.name != name:
            raise HTTPException(403)
        from ..collector.session_lock import session_lock
        from contextlib import nullcontext
        try:
            with session_lock(target_path, blocking=False) if valid else nullcontext():
                row.status = "success" if valid else "verification_failed"
                if valid:
                    session.add(CollectorRun(platform=platform, account_id=account_id, triggered_by="verify",
                        status="success", started_at=func.now(), finished_at=func.now()))
                await log_operation(str(user_id), "collector_repair_finish", {"repair_id": identifier,
                    "platform": platform, "account_id": account_id, "status": row.status}, session=session)
                await session.flush()
                result = {"status": row.status}
                previous = target_path.read_bytes() if valid and target_path.exists() else None
                if valid:
                    os.replace(candidate, target_path)
                try:
                    await session.commit()
                except Exception:
                    if valid:
                        if previous is None:
                            target_path.unlink(missing_ok=True)
                        else:
                            # Same private temporary file and atomic rollback.
                            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(previous)
                            os.replace(candidate, target_path)
                    raise
                return result
        except BlockingIOError:
            row.status = "collector_busy"
            await session.commit()
            return {"status": "collector_busy"}

    finally:
        candidate.unlink(missing_ok=True)
        candidate.with_suffix(candidate.suffix + ".lock").unlink(missing_ok=True)


@router.get("/download/{filename}")
async def download_helper(filename: str):
    from fastapi.responses import FileResponse
    if filename not in {"omnipanel_collector_login-1.0.0-py3-none-any.whl", "omnipanel-collector-login-connector.zip", "omnipanel-collector-login-skill.zip"}:
        raise HTTPException(404)
    path = Path(__file__).resolve().parents[2] / "integrations/collector-login/dist" / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=filename, media_type="application/octet-stream")

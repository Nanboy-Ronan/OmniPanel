# rap/app/views/admin.py
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from fastapi_users import exceptions
from fastapi_users.router.common import ErrorCode
from sqlalchemy import select, func, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    current_admin_user,
    get_user_manager,
    UserCreate,
    UserManager,
)

router = APIRouter(prefix="/admin", tags=["admin"])

from ..db import get_session
from ..db.backup import backup_database
from ..db.models import (
    User, Order, Customer, OperationLog,
    UploadBatch, UploadRejectedRow,
    YouzanOrder, JdOrder, TmallOrder,
)
from ..utils.logger import log_operation
from ..utils.cache import analysis_cache

logger = logging.getLogger(__name__)


@router.post("/clear-db")
async def clear_database(
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Drop all "data" tables, except for the FastAPI-Users 'user' table
    (and any Alembic tables). This preserves your user accounts but wipes orders, customers, etc.
    """
    try:
        backup_path = await asyncio.to_thread(backup_database, "before-clear-db")

        # Delete business data while preserving user accounts.
        # Order matters: children before parents (FK constraints).
        await session.execute(delete(UploadRejectedRow))
        await session.execute(delete(YouzanOrder))
        await session.execute(delete(JdOrder))
        await session.execute(delete(TmallOrder))
        await session.execute(delete(UploadBatch))
        await session.execute(delete(Order))
        await session.execute(delete(Customer))
        await session.execute(delete(OperationLog))

        await session.commit()

    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear database: {e}",
        )

    try:
        await analysis_cache.invalidate()
    except Exception:
        logger.exception("Database cleared, but analysis cache invalidation failed")
    try:
        await log_operation(
            str(_user.id),
            "clear_db",
            {
                "dropped_tables": [
                    "upload_rejected_rows",
                    "youzan_orders",
                    "jd_orders",
                    "tmall_orders",
                    "upload_batches",
                    "orders",
                    "customers",
                    "operation_log",
                ],
                "backup": str(backup_path) if backup_path else None,
            },
            session=session,
        )
    except Exception:
        await session.rollback()
        logger.exception("Database cleared, but audit logging failed")

    return {
        "detail": (
            "Dropped tables: upload_rejected_rows, youzan_orders, jd_orders, "
            "tmall_orders, upload_batches, orders, customers, operation_log"
        ),
        "backup_path": str(backup_path) if backup_path else None,
    }


@router.get("/users")
async def list_users(
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Return all user accounts and their roles."""
    result = await session.execute(
        select(User.id, User.email, User.role, User.is_active, User.wecom_userid, User.wecom_alert_enabled)
    )
    rows = [
        {
            "id": str(r.id), "email": r.email, "role": r.role, "is_active": r.is_active,
            "wecom_linked": r.wecom_userid is not None, "wecom_alert_enabled": r.wecom_alert_enabled,
        }
        for r in result.all()
    ]
    return rows


@router.get("/db-status")
async def database_status(
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Inspect actual tables and columns rather than reporting a static schema."""
    from .database_status import inspect_database, backup_inventory

    try:
        result = await inspect_database(session)
    except Exception:
        logger.exception("Database status inspection failed")
        raise HTTPException(status_code=503, detail="数据库状态检查失败，请稍后重试或查看服务日志。")
    result["backups"] = await asyncio.to_thread(backup_inventory)
    return result


class NewUser(BaseModel):
    email: str
    password: str
    role: str | None = None


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: NewUser,
    request: Request,
    user_manager: UserManager = Depends(get_user_manager),
    _user=Depends(current_admin_user),
):
    """Create a new user account."""
    # Accounts normally sign in via WeCom; a password account is a fallback for
    # local tooling, so it must not be guessable.
    if len(payload.password) < 12 or payload.password.lower() == payload.email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码至少 12 位，且不能与邮箱相同。",
        )
    user_data = UserCreate(
        email=payload.email,
        password=payload.password,
        role=payload.role or "viewer",
    )
    try:
        created = await user_manager.create(user_data, safe=True, request=request)
    except exceptions.UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.REGISTER_USER_ALREADY_EXISTS,
        )
    except exceptions.InvalidPasswordException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": ErrorCode.REGISTER_INVALID_PASSWORD, "reason": e.reason},
        )

    await log_operation(
        str(_user.id),
        "create_user",
        {"email": created.email, "role": created.role},
    )
    return {"id": str(created.id), "email": created.email, "role": created.role}


class RoleUpdate(BaseModel):
    role: str


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    payload: RoleUpdate,
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Update another user's role."""
    role = payload.role
    if role not in {"viewer", "analyst", "admin"}:
        raise HTTPException(status_code=400, detail="Invalid role")

    try:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user.role = role
        await session.commit()

        await log_operation(
            str(_user.id), "update_role", {"target_user": user_id, "new_role": role},
            session=session,
        )
        return {"detail": "Role updated"}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update role: {e}")


class ActiveUpdate(BaseModel):
    is_active: bool


@router.put("/users/{user_id}/active")
async def update_user_active(
    user_id: str,
    payload: ActiveUpdate,
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Enable or disable a user account."""
    try:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        if str(user.id) == str(_user.id):
            raise HTTPException(status_code=400, detail="Cannot change your own active status.")
        user.is_active = payload.is_active
        await session.commit()
        await log_operation(
            str(_user.id), "update_active",
            {"target_user": user_id, "is_active": payload.is_active},
            session=session,
        )
        return {"detail": "Active status updated"}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update active status: {e}")


class WecomAlertUpdate(BaseModel):
    enabled: bool


@router.put("/users/{user_id}/wecom-alert")
async def update_user_wecom_alert(
    user_id: str,
    payload: WecomAlertUpdate,
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Toggle whether a user receives WeCom collector/pipeline alerts."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.wecom_userid is None and payload.enabled:
        raise HTTPException(status_code=400, detail="该用户尚未绑定企业微信，无法接收告警")
    user.wecom_alert_enabled = payload.enabled
    await session.commit()
    await log_operation(
        str(_user.id), "update_wecom_alert",
        {"target_user": user_id, "enabled": payload.enabled},
        session=session,
    )
    return {"detail": "WeCom alert setting updated"}


class PasswordUpdate(BaseModel):
    password: str


@router.put("/users/{user_id}/password")
async def update_user_password(
    user_id: str,
    payload: PasswordUpdate,
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Reset another user's password."""
    try:
        from ..auth import _password_helper

        helper = _password_helper()
        hashed = helper.hash(payload.password)

        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user.hashed_password = hashed
        await session.commit()

        await log_operation(
            str(_user.id), "update_password", {"target_user": user_id},
            session=session,
        )
        return {"detail": "Password updated"}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update password: {e}")


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_account(
    user_id: str,
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Delete a user account (admin only). Cannot delete your own account."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(_user.id):
        raise HTTPException(status_code=400, detail="Cannot delete your own account.")
    email = user.email
    await session.delete(user)
    await session.commit()
    await log_operation(str(_user.id), "delete_user", {"target_user": user_id, "email": email}, session=session)


# Audit categories for the log page filter. Actions not listed fall under "other".
LOG_CATEGORIES: dict[str, tuple[str, ...]] = {
    "auth": (
        "login", "wecom_login", "login_failed", "wecom_login_failed", "logout",
        "register", "wecom_register",
    ),
    "access": ("download", "export_client", "view_customer", "view_order_raw", "sql_query", "nl_sql_query"),
    "data": (
        "upload", "xhs_upload", "xhs_upload_overview", "zhihu_upload", "pgy_upload", "channels_upload",
        "wechat_sync", "clear_db", "weekly_report_run",
        "media_account_create", "xhs_account_create", "xhs_account_update", "xhs_account_delete",
        "channels_account_create", "channels_account_update", "channels_account_delete",
        "collector_session_upload", "collector_session_delete",
        "saved_query_create", "saved_query_delete",
    ),
    "admin": (
        "create_user", "update_role", "update_active", "update_password",
        "update_wecom_alert", "delete_user",
    ),
}


@router.get("/logs")
async def get_logs(
    response: Response,
    user_id: str | None = None,
    category: str | None = Query(None, pattern="^(auth|access|data|admin|failed)$"),
    start_date: date | None = None,
    end_date: date | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Operation logs, newest first. Sign-in attempts with no account are included.

    The body stays a list for existing clients; the matching total is in
    ``X-Total-Count`` for pagination.
    """
    conditions = []
    if user_id:
        conditions.append(OperationLog.user_id == user_id)
    if category == "failed":
        conditions.append(OperationLog.action.in_(("login_failed", "wecom_login_failed")))
    elif category:
        conditions.append(OperationLog.action.in_(LOG_CATEGORIES[category]))
    if start_date:
        conditions.append(OperationLog.timestamp >= datetime.combine(start_date, time.min))
    if end_date:
        conditions.append(OperationLog.timestamp < datetime.combine(end_date + timedelta(days=1), time.min))
    if q:
        pattern = f"%{q}%"
        conditions.append(or_(
            OperationLog.detail.ilike(pattern), User.email.ilike(pattern), OperationLog.action.ilike(pattern),
        ))

    base = select(OperationLog.id).outerjoin(User, OperationLog.user_id == User.id).where(*conditions)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    stmt = (
        select(
            OperationLog.id,
            User.email.label("email"),
            OperationLog.action,
            OperationLog.timestamp,
            OperationLog.detail,
        )
        .outerjoin(User, OperationLog.user_id == User.id)
        .where(*conditions)
        .order_by(OperationLog.timestamp.desc(), OperationLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    rows = []
    for r in result.all():
        detail = json.loads(r.detail) if r.detail else None
        context = detail if isinstance(detail, dict) else {}
        rows.append({
            "id": r.id,
            "email": r.email,
            "action": r.action,
            "timestamp": str(r.timestamp) if r.timestamp else None,
            "ip": context.get("ip"),
            "user_agent": context.get("user_agent"),
            "detail": {k: v for k, v in context.items() if k not in ("ip", "user_agent")} or None
            if isinstance(detail, dict) else detail,
        })
    response.headers["X-Total-Count"] = str(total)
    return rows

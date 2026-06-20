# rap/app/views/admin.py
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from fastapi_users import exceptions
from fastapi_users.router.common import ErrorCode
from sqlalchemy import select, func, delete
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
        backup_path = backup_database("before-clear-db")

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

        await analysis_cache.invalidate()
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

        return {
            "detail": (
                "Dropped tables: upload_rejected_rows, youzan_orders, jd_orders, "
                "tmall_orders, upload_batches, orders, customers, operation_log"
            ),
            "backup_path": str(backup_path) if backup_path else None,
        }

    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear database: {e}",
        )


@router.get("/users")
async def list_users(
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Return all user accounts and their roles."""
    result = await session.execute(select(User.id, User.email, User.role, User.is_active))
    rows = [
        {"id": str(r.id), "email": r.email, "role": r.role, "is_active": r.is_active}
        for r in result.all()
    ]
    return rows


@router.get("/db-status")
async def database_status(
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Return database state useful for deployment diagnostics."""
    try:
        result = await session.execute(select(func.count(Order.id)))
        row_count = result.scalar() or 0
        analysis_ready = row_count > 0
    except Exception:
        row_count = None
        analysis_ready = False

    return {
        "database_path": "PostgreSQL",
        "database_exists": True,
        "tables": [
            "user",
            "customers",
            "orders",
            "upload_batches",
            "youzan_orders",
            "jd_orders",
            "tmall_orders",
            "upload_rejected_rows",
            "media_accounts",
            "media_posts",
            "media_post_metrics_daily",
            "media_sync_runs",
            "operation_log",
        ],
        "analysis_ready": analysis_ready,
        "missing_analysis_tables": [],
        "missing_analysis_mappings": [],
        "missing_analysis_columns": [],
        "all_orders_count": row_count,
    }


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


@router.get("/logs")
async def get_logs(
    user_id: str | None = None,
    _user=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Return recent operation logs with user emails."""
    stmt = (
        select(
            OperationLog.id,
            User.email.label("email"),
            OperationLog.action,
            OperationLog.timestamp,
            OperationLog.detail,
        )
        .join(User, OperationLog.user_id == User.id)
        .order_by(OperationLog.timestamp.desc())
        .limit(100)
    )
    if user_id:
        stmt = stmt.where(OperationLog.user_id == user_id)

    result = await session.execute(stmt)
    rows = [
        {
            "id": r.id,
            "email": r.email,
            "action": r.action,
            "timestamp": str(r.timestamp) if r.timestamp else None,
            "detail": json.loads(r.detail) if r.detail else None,
        }
        for r in result.all()
    ]
    return rows

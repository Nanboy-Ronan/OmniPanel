"""Custom register route with initial-user restriction."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users import exceptions, schemas
from fastapi_users.router.common import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from ..auth import (
    get_user_manager,
    UserCreate,
    UserRead,
    UserManager,
    current_active_user,
    require_no_users,
)
from ..db import get_session
from ..db.models import User
from ..utils.logger import log_operation

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    name="register:register",
)
async def register(
    request: Request,
    user_create: UserCreate,
    user_manager: UserManager = Depends(get_user_manager),
    _=Depends(require_no_users),
):
    try:
        created_user = await user_manager.create(user_create, safe=True, request=request)
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

    await log_operation(str(created_user.id), "register", {"email": created_user.email})
    return schemas.model_validate(UserRead, created_user)


@router.get("/register/open", name="register:open")
async def register_open(session: AsyncSession = Depends(get_session)) -> dict:
    """Return whether new registrations are allowed."""
    result = await session.execute(select(func.count(User.id)))
    count = result.scalar_one()
    return {"allowed": count == 0}


@router.get("/me", name="auth:me")
async def me(user: User = Depends(current_active_user)) -> dict:
    """Return the current user's identity and role."""
    display = user.display_name or user.email.split("@")[0]
    return {"id": str(user.id), "email": user.email, "role": user.role, "display_name": display}


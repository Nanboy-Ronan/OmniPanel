# rap/app/auth.py
from __future__ import annotations
import logging
import uuid
from datetime import timedelta
from fastapi import Depends, Request, HTTPException, status
from fastapi_users import FastAPIUsers, UUIDIDMixin, BaseUserManager, schemas
from fastapi_users.password import PasswordHelper
from fastapi_users.authentication import (
    BearerTransport, AuthenticationBackend, JWTStrategy
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from .config import settings
from .db import get_session
from .db.models import User
from .utils.logger import log_operation

logger = logging.getLogger(__name__)

SECRET = settings.rap_secret
if SECRET == "CHANGE_ME":
    raise RuntimeError(
        "RAP_SECRET is not configured. Set the RAP_SECRET environment variable "
        "to a secure random value before starting the application."
    )
TOKEN_LIFETIME = settings.token_lifetime_seconds

# ─── Pydantic schemas ────────────────────────────────────────────────
class UserRead(schemas.BaseUser[uuid.UUID]):
    role: str


class UserCreate(schemas.BaseUserCreate):
    role: str = "viewer"

# ─── DB dependency that yields AsyncSession ──────────────────────────
async def get_user_db(
    session: AsyncSession = Depends(get_session),
):
    yield SQLAlchemyUserDatabase(session, User)

# ─── UserManager (needed by FastAPI-Users v12+) ──────────────────────
class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request: Request | None = None):
        # If this is the very first registered user, promote them to admin
        session = self.user_db.session
        result = await session.execute(select(func.count(User.id)))
        count = result.scalar_one()
        if count == 1:
            await self.user_db.update(user, {"role": "admin"})

        logger.info("registered: %s", user.email)

    async def on_after_login(
        self, user: User, request: Request | None = None, response=None
    ) -> None:
        await log_operation(str(user.id), "login")

async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db, _password_helper())         # FastAPI-Users will await its methods


def _password_helper():
    return PasswordHelper()

# ─── Auth backend (JWT bearer) ────────────────────────────────────────
bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")

def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=TOKEN_LIFETIME)

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)

current_active_user = fastapi_users.current_user(active=True)


def current_admin_user(user=Depends(current_active_user)):
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def current_analyst_user(user=Depends(current_active_user)):
    if user.role not in {"admin", "analyst"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Analyst access required")
    return user


async def require_no_users(session: AsyncSession = Depends(get_session)):
    """Allow registration only if no users exist."""
    result = await session.execute(select(func.count(User.id)))
    if result.scalar_one() > 0:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration closed")

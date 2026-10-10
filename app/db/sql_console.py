"""Connection for the ad hoc SQL console, logged in as ``rpa_sql_console``.

Console SQL must never run on the application connection: a role switch made on
that connection can be undone by the SQL itself. This engine authenticates as a
role that can only read the ``reporting`` views, so there is nothing to escape to.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.config import settings

_engine: AsyncEngine | None = None
_engine_url: str | None = None


class SqlConsoleNotConfigured(RuntimeError):
    """RAP_SQL_CONSOLE_DATABASE_URL is unset; the console stays disabled."""

    def __init__(self, message: str = "SQL 控制台尚未配置只读数据库账号，请联系管理员。"):
        super().__init__(message)


def _get_engine() -> AsyncEngine:
    global _engine, _engine_url
    url = settings.sql_console_database_url
    if not url:
        raise SqlConsoleNotConfigured()
    if _engine is None or _engine_url != url:
        _engine = create_async_engine(
            url,
            future=True,
            pool_size=2,
            max_overflow=2,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={"server_settings": {"timezone": settings.app_timezone}},
        )
        _engine_url = url
    return _engine


async def dispose_engine() -> None:
    global _engine, _engine_url
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _engine_url = None


@asynccontextmanager
async def console_session() -> AsyncIterator[AsyncSession]:
    """A read-only, time-limited transaction as the console role."""
    async with AsyncSession(_get_engine(), expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(text("SET LOCAL search_path TO reporting, pg_catalog"))
            await session.execute(text("SET LOCAL transaction_read_only = on"))
            await session.execute(text("SET LOCAL statement_timeout = '10000'"))
            yield session

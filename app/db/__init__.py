# rap/app/db/__init__.py

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, Session, declarative_base

from app.config import settings

# Keep module-level DATABASE_URL for backward-compat (backup.py imports it).
DATABASE_URL = settings.rap_database_url

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.db_echo,
    future=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=settings.db_pool_recycle,
    connect_args={
        "server_settings": {
            "timezone": settings.app_timezone,
        }
    },
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

def sync_connect_args(tz_name: str | None = None) -> dict:
    """psycopg2 connect_args giving the sync engine the same session
    timezone the async engine sets via server_settings.

    Without it the sync engine ran in the server default (UTC on the VM),
    so server-generated naive timestamps it wrote (collector_runs.started_at,
    the media-ETL created_at/updated_at columns) were UTC while everything
    written through the async engine was APP_TIMEZONE local time. Historical
    rows are converted by alembic revision 0020_sync_engine_tz_backfill.
    """
    return {"options": f"-c timezone={tz_name or settings.app_timezone}"}


# Synchronous engine for use inside asyncio.to_thread() — psycopg2 driver,
# same pool settings and session timezone as the async engine.
_sync_url = DATABASE_URL.replace("+asyncpg", "+psycopg2")
sync_engine = create_engine(
    _sync_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=settings.db_pool_recycle,
    connect_args=sync_connect_args(),
)

SyncSessionLocal = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

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

# Synchronous engine for use inside asyncio.to_thread() — psycopg2 driver,
# same pool settings, no asyncpg-specific connect_args.
_sync_url = DATABASE_URL.replace("+asyncpg", "+psycopg2")
sync_engine = create_engine(
    _sync_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=settings.db_pool_recycle,
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

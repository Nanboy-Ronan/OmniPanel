"""Alembic migration environment for rpa-data (async SQLAlchemy + asyncpg)."""
import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Make the project root importable when running `alembic` from the repo root.
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Alembic Config object — provides access to alembic.ini values.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the URL from the environment (same source of truth as app/db/__init__.py).
DATABASE_URL = os.getenv(
    "RAP_DATABASE_URL",
    "postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa",
)
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Register all ORM models so their metadata is visible to autogenerate.
from app.db import Base  # noqa: E402
import app.db.models  # noqa: F401, E402

target_metadata = Base.metadata


# ── Offline mode (generate SQL without connecting) ────────────────────────────

def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (connect and apply migrations) ────────────────────────────────

def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(DATABASE_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

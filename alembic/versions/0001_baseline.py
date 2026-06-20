"""baseline: create all tables from ORM models.

This is the initial migration that captures the schema as it existed before
Alembic was introduced.  On an **existing** production database, run:

    alembic stamp 0001_baseline

to record that the database is already at this revision without re-creating
anything.  On a **fresh** database, run:

    alembic upgrade head

which executes ``upgrade()`` and creates all tables.

Revision ID: 0001_baseline
Revises: (none)
Create Date: 2026-05-25
"""
from typing import Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all tables defined in the ORM metadata (IF NOT EXISTS)."""
    import app.db.models  # noqa: F401 — registers dynamic models (youzan/jd/tmall)
    from app.db import Base

    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Drop all tables defined in the ORM metadata."""
    import app.db.models  # noqa: F401
    from app.db import Base

    Base.metadata.drop_all(op.get_bind())

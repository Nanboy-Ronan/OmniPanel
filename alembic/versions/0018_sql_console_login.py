"""Give the SQL console its own login role instead of SET ROLE on the app connection.

0017 ran ad hoc SQL on the application's connection after ``SET LOCAL ROLE
rpa_analytics_readonly``. User SQL could undo that with
``set_config('role', 'none', true)``, which falls back to the session user —
the application role — and read every public table. A separate LOGIN role whose
*session* identity is already restricted closes that: resetting the role lands
back on ``rpa_sql_console``, which can only read the masked ``reporting`` views.

The role is created without a password. Operations sets one
(``ALTER ROLE rpa_sql_console PASSWORD ...``) and points
``RAP_SQL_CONSOLE_DATABASE_URL`` at it; until then the console reports that it
is not configured rather than falling back to the application connection.
"""

from alembic import op
from sqlalchemy import text

revision = "0018_sql_console_login"
down_revision = "0017_reporting_role"
branch_labels = None
depends_on = None

ROLE = "rpa_sql_console"
READONLY = "rpa_analytics_readonly"


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
            CREATE ROLE {ROLE} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
        END IF;
    END $$"""))
    bind.execute(text(f"GRANT {READONLY} TO {ROLE}"))
    database = _quote(bind.engine.url.database)
    bind.execute(text(f"GRANT CONNECT ON DATABASE {database} TO {ROLE}"))
    # Defaults the console also sets per transaction; here they hold even if a
    # statement manages to reset its own transaction settings.
    bind.execute(text(f"ALTER ROLE {ROLE} IN DATABASE {database} SET search_path = reporting, pg_catalog"))
    bind.execute(text(f"ALTER ROLE {ROLE} IN DATABASE {database} SET default_transaction_read_only = on"))
    bind.execute(text(f"ALTER ROLE {ROLE} IN DATABASE {database} SET statement_timeout = '10s'"))


def downgrade() -> None:
    # The role is cluster-wide and may serve other databases; only remove this
    # database's grants and settings.
    bind = op.get_bind()
    database = _quote(bind.engine.url.database)
    bind.execute(text(f"ALTER ROLE {ROLE} IN DATABASE {database} RESET ALL"))
    bind.execute(text(f"REVOKE CONNECT ON DATABASE {database} FROM {ROLE}"))

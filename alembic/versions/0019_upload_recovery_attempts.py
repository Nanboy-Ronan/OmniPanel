"""Count crash-recovery claims per upload batch so poison files stop being retried.

app/db/maintenance.recover_uploads re-claims "processing"/"recovering"
batches older than two hours. A file that crashes or hangs the worker was
re-claimed every minute forever; the counter lets it be failed after
MAX_RECOVERY_ATTEMPTS claims.
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_upload_recovery_attempts"
down_revision = "0018_sql_console_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_batches",
        sa.Column("recovery_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("upload_batches", "recovery_attempts")

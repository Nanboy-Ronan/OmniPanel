"""Keep each order's platform status and refunds so revenue can exclude cancelled orders.

Adds ``orders.raw_status``, ``orders.status_group`` and ``orders.refunded_amount``
and backfills them from the raw platform tables (``youzan_orders`` etc.), which
already hold every exported column. Orders with no raw row stay ``unknown`` and
keep counting toward revenue. The masked ``reporting.orders`` view gains the
three columns so the SQL console can apply the same rule.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

from app.db.order_status import classify

revision = "0022_order_status"
down_revision = "0021_operation_log_audit"
branch_labels = None
depends_on = None

ROLE = "rpa_analytics_readonly"
NEW_COLUMNS = ("raw_status", "status_group", "refunded_amount")
# Same exclusions as 0017_reporting_role.
EXCLUDED = {"customer_key", "receiver", "receiver_phone", "full_address", "buyer_nick"}
RAW_TABLES = {"youzan": "youzan_orders", "jd": "jd_orders", "tmall": "tmall_orders"}
REFUND_SQL = """CASE WHEN regexp_replace(btrim(coalesce(r.refund, '')), '[,¥]', '', 'g') ~ '^[0-9]+(\\.[0-9]+)?$'
    THEN nullif(round(regexp_replace(btrim(r.refund), '[,¥]', '', 'g')::numeric, 2), 0) END"""


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _recreate_view(bind, *, drop_new: bool) -> None:
    if not bind.execute(text("SELECT to_regclass('reporting.orders')")).scalar():
        return
    columns = [
        c["name"] for c in inspect(bind).get_columns("orders", schema="public")
        if c["name"] not in EXCLUDED and not (drop_new and c["name"] in NEW_COLUMNS)
    ]
    projection = ", ".join(_quote(c) for c in columns)
    if drop_new:
        bind.execute(text("DROP VIEW reporting.orders"))
    bind.execute(text(f"CREATE OR REPLACE VIEW reporting.orders AS SELECT {projection} FROM public.orders"))
    bind.execute(text(f"GRANT SELECT ON reporting.orders TO {ROLE}"))


def _backfill(bind) -> None:
    tables = set(inspect(bind).get_table_names(schema="public"))
    for platform, table in RAW_TABLES.items():
        if table not in tables:
            continue
        columns = {c["name"] for c in inspect(bind).get_columns(table, schema="public")}
        if "订单状态" not in columns:
            continue
        has_refund = platform == "youzan" and "订单已退款金额" in columns
        refund_select = ', "订单已退款金额" AS refund' if has_refund else ""
        refund_set = f", refunded_amount = {REFUND_SQL}" if has_refund else ""
        # JD keeps one raw row per order line; the first line carries the order's status.
        bind.execute(text(f"""
            UPDATE orders o SET raw_status = left(nullif(btrim(r.status), ''), 64){refund_set}
            FROM (
                SELECT DISTINCT ON (normalized_order_id) normalized_order_id, "订单状态" AS status{refund_select}
                FROM {_quote(table)} ORDER BY normalized_order_id, id
            ) r
            WHERE o.platform = :platform AND o.order_id = r.normalized_order_id
        """), {"platform": platform})
    pairs = bind.execute(text(
        "SELECT DISTINCT platform, raw_status FROM orders WHERE raw_status IS NOT NULL"
    )).all()
    for platform, raw_status in pairs:
        group = classify(platform, raw_status)
        if group != "unknown":
            bind.execute(
                text("UPDATE orders SET status_group = :g WHERE platform = :p AND raw_status = :s"),
                {"g": group, "p": platform, "s": raw_status},
            )


def upgrade() -> None:
    op.add_column("orders", sa.Column("raw_status", sa.String(64), nullable=True))
    op.add_column(
        "orders",
        sa.Column("status_group", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.add_column("orders", sa.Column("refunded_amount", sa.Numeric(12, 2), nullable=True))
    op.create_index("ix_orders_platform_order_id", "orders", ["platform", "order_id"])
    bind = op.get_bind()
    _backfill(bind)
    _recreate_view(bind, drop_new=False)


def downgrade() -> None:
    _recreate_view(op.get_bind(), drop_new=True)
    op.drop_index("ix_orders_platform_order_id", table_name="orders")
    op.drop_column("orders", "refunded_amount")
    op.drop_column("orders", "status_group")
    op.drop_column("orders", "raw_status")

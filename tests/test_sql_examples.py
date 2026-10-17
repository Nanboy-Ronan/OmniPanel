"""SQL console examples must follow the same revenue rule as the dashboards."""
import json
from pathlib import Path

from app.db.order_status import counted_sql

EXAMPLES = json.loads((Path(__file__).resolve().parents[1] / "frontend/src/lib/sqlExamples.json").read_text())


def test_order_examples_filter_status_and_net_refunds():
    rule = counted_sql()
    for title, sql in EXAMPLES:
        if "FROM orders" not in sql or "NOT IN" in sql:
            continue
        assert rule in sql, title
        if "price" in sql:
            assert "price - COALESCE(refunded_amount, 0)" in sql, title
            assert "SUM(price)" not in sql and "AVG(price)" not in sql, title


def test_examples_only_use_columns_exposed_to_the_console():
    for title, sql in EXAMPLES:
        assert "customer_key" not in sql, title  # excluded from reporting.orders

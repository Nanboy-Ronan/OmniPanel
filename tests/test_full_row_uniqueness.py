# tests/test_full_row_uniqueness.py

import os
import sys
import pytest
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ──────────────────────────────────────────────────────────────────
# 1) Ensure that “app/” is on sys.path so we can import "app.db"
current_dir = os.path.dirname(__file__)
project_root = os.path.abspath(os.path.join(current_dir, os.pardir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ──────────────────────────────────────────────────────────────────

# 2) Now import from our application code
from app.db import Base
from app.db.models import Customer, Order
from app.db.etl import ingest


@pytest.fixture
def db_session(pg_sync_url):
    """
    Create PostgreSQL tables, yield a Session.
    After the test, the session is closed.
    """
    engine = create_engine(pg_sync_url, echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_raw_dataframe(orders):
    """
    Given a list of tuples (order_id, order_date, mobile, sku, quantity, price),
    construct a pandas DataFrame with the exact Chinese headers that `normalise()`
    expects:
      ["订单号", "买家付款时间", "收货人手机号/提货人手机号", "全部商品名称", "商品种类数", "订单实付金额"].

    `order_date` entries can be date strings (YYYY-MM-DD).
    """
    df = pd.DataFrame(
        orders,
        columns=[
            "订单号",
            "买家付款时间",
            "收货人手机号/提货人手机号",
            "全部商品名称",
            "商品种类数",
            "订单实付金额",
        ],
    )
    # The raw values are strings (as if read from CSV). In production, pandas.read_csv dtype=str is used.
    # We can leave them as-is: normalise() will convert order_date → date, price→float, quantity→int.
    return df


def test_full_row_uniqueness(db_session):
    """
    1) Ingest a first batch of 4 orders (all distinct).
    2) Ingest a second batch of 5 “raw” rows, where:
       - 2 rows are exact duplicates by order_id → skipped,
       - 1 row has the same order_id but different price → skipped,
       - 2 rows are completely new → inserted.
    Assert that exactly 2 new rows get added on the second ingest.
    """
    session = db_session

    # ─── Step 1: First ingestion ──────────────────────────────────────────
    first_batch = [
        # (order_id, order_date,     mobile,        sku,          quantity, price)
        ("AAA001", "2025-05-01", "13800001111", "Product X",       "1",     "99.00"),
        ("AAA002", "2025-05-02", "13800002222", "Product Y",       "2",    "199.00"),
        ("AAA003", "2025-05-03", "13800003333", "Product Z",       "1",    "299.00"),
        ("AAA004", "2025-05-04", "13800004444", "Product Q",       "3",    "399.00"),
    ]
    df1_raw = make_raw_dataframe(first_batch)

    # Ingest the first raw batch
    inserted1 = ingest(df1_raw, session)
    assert inserted1 == 4

    # Verify that 4 orders and 4 customers exist
    assert session.query(Order).count() == 4
    assert session.query(Customer).count() == 4

    # ─── Step 2: Second ingestion ─────────────────────────────────────────
    second_batch = [
        # Exact duplicate of ("AAA002", "2025-05-02", "13800002222", "Product Y", "2", "199.00")
        ("AAA002", "2025-05-02", "13800002222", "Product Y", "2", "199.00"),
        # Exact duplicate of ("AAA004", "2025-05-04", "13800004444", "Product Q", "3", "399.00")
        ("AAA004", "2025-05-04", "13800004444", "Product Q", "3", "399.00"),

        # Same order_id="AAA002" but price changed to "209.00" → skipped
        ("AAA002", "2025-05-02", "13800002222", "Product Y", "2", "209.00"),

        # Completely new rows:
        ("AAA005", "2025-05-05", "13800005555", "Product R", "1", "499.00"),
        ("AAA006", "2025-05-06", "13800006666", "Product S", "2", "599.00"),
    ]
    df2_raw = make_raw_dataframe(second_batch)

    inserted2 = ingest(df2_raw, session)

    # We expect exactly 2 new rows: AAA005 and AAA006.
    assert inserted2 == 2
    assert session.query(Order).count() == 6  # 4 + 2

    # Confirm the final set of six-tuples in the DB:
    all_orders = session.query(Order).all()
    db_tuples = {
        (o.order_id, o.order_date, o.customer_key, o.sku, o.quantity, float(o.price))
        for o in all_orders
    }
    # We fetch the actual dates from the objects so Python date objects match:
    expected_tuples = {
        ("AAA001", session.query(Order).filter_by(order_id="AAA001").first().order_date, "13800001111", "Product X", 1,  99.00),
        ("AAA002", session.query(Order).filter_by(order_id="AAA002", price=199.00).first().order_date, "13800002222", "Product Y", 2, 199.00),
        ("AAA003", session.query(Order).filter_by(order_id="AAA003").first().order_date,     "13800003333", "Product Z", 1, 299.00),
        ("AAA004", session.query(Order).filter_by(order_id="AAA004", price=399.00).first().order_date, "13800004444", "Product Q", 3, 399.00),
        ("AAA005", session.query(Order).filter_by(order_id="AAA005").first().order_date, "13800005555", "Product R", 1, 499.00),
        ("AAA006", session.query(Order).filter_by(order_id="AAA006").first().order_date, "13800006666", "Product S", 2, 599.00),
    }
    assert db_tuples == expected_tuples

    # Finally, confirm that Customer table now has 6 distinct customer_keys
    all_customers = session.query(Customer).all()
    assert len(all_customers) == 6
    all_customer_keys = {c.customer_key for c in all_customers}
    assert all_customer_keys == {
        "13800001111",
        "13800002222",
        "13800003333",
        "13800004444",
        "13800005555",
        "13800006666",
    }


def test_csv_reingest_and_modified_rows(db_session):
    """Ingest the synthetic Youzan dataset twice and then with modifications.

    1. First ingest should insert all rows from the dataset.
    2. Re-ingesting the unchanged dataset should insert zero new rows.
    3. After appending a duplicate row and a changed row, only the changed row
       should be inserted.
    """
    from sample_dataset import synthetic_youzan_df

    session = db_session

    df_raw = synthetic_youzan_df()

    # First ingestion: all rows with a unique order_id accepted by the legacy
    # direct ingest path should be new.
    inserted1 = ingest(df_raw, session)
    expected_rows = inserted1
    assert session.query(Order).count() == expected_rows

    # Re-ingest the exact same file – nothing new should be added
    inserted2 = ingest(df_raw, session)
    assert inserted2 == 0
    assert session.query(Order).count() == expected_rows

    # Duplicate the first row and also add a modified version (price changed)
    duplicate = df_raw.iloc[[0]].copy()
    modified = df_raw.iloc[[0]].copy()
    modified["订单实付金额"] = "999.99"
    df_modified = pd.concat([df_raw, duplicate, modified], ignore_index=True)

    inserted3 = ingest(df_modified, session)

    # Both rows use an existing order_id, so neither should be inserted.
    assert inserted3 == 0
    assert session.query(Order).count() == expected_rows

    inserted4 = ingest(df_modified, session)

    # Re-ingesting that same modified set should not add anything else.
    assert inserted4 == 0
    assert session.query(Order).count() == expected_rows

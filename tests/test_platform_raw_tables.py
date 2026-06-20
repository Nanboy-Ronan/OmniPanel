"""Specification tests for platform-specific raw order tables.

These tests describe the target behavior for the raw-data refactor:

* every platform gets its own table with all original upload headers;
* duplicate order uploads do not duplicate rows in either ``orders`` or the
  platform table;
* invalid source rows are retained in ``upload_rejected_rows``;
* JD source rows keep their original row granularity even when multiple rows
  normalize to a single analytics order.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from test_api_endpoints import client, tokens  # noqa: F401
from conftest import upload_and_poll  # noqa: F401


SYSTEM_PLATFORM_COLUMNS = {
    "id",
    "batch_id",
    "source_row_number",
    "order_id",
    "normalized_order_id",
    "ingest_status",
    "ingest_message",
    "row_hash",
    "created_at",
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sync_engine(pg_sync_url):
    engine = create_engine(pg_sync_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()




def _table_columns(engine, table_name: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).all()
    return {row.column_name for row in rows}


def _scalar(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _row(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).mappings().one()


def test_upload_preserves_unmapped_youzan_fields_in_platform_table(client, tokens, sync_engine):
    """Fields that are not in normalized orders must still be queryable later."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,"
        "订单实付金额,订单状态,买家备注,商家订单备注\n"
        "YZ-RAW-1,2025-07-21 10:00:00,13800138000,商品甲,1,88.50,"
        "交易成功,请周末派送,客服确认过地址"
    )

    body = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "youzan_raw.csv")
    assert body["platform"] == "youzan"
    assert body["inserted_orders"] == 1
    assert body["duplicate_rows"] == 0
    assert body["invalid_rows"] == 0

    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM youzan_orders") == 1

    row = _row(
        sync_engine,
        '''
        SELECT "订单状态", "买家备注", "商家订单备注", ingest_status, normalized_order_id
        FROM youzan_orders
        WHERE "订单号" = :order_id
        ''',
        order_id="YZ-RAW-1",
    )
    assert row["订单状态"] == "交易成功"
    assert row["买家备注"] == "请周末派送"
    assert row["商家订单备注"] == "客服确认过地址"
    assert row["ingest_status"] == "inserted"
    assert row["normalized_order_id"] is not None


def test_duplicate_order_is_not_inserted_into_orders_or_platform_table(
    client, tokens, sync_engine
):
    """A duplicate order upload should only create a new batch-level record."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-DUP-1,2025-07-21 10:00:00,13800138000,商品甲,1,88.50"
    )

    first = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "dup_1.csv")
    second = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "dup_2.csv")
    assert first["inserted_orders"] == 1
    assert second["inserted_orders"] == 0
    assert second["duplicate_rows"] == 1

    assert _scalar(sync_engine, "SELECT count(*) FROM upload_batches") == 2
    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM youzan_orders") == 1


def test_invalid_source_row_is_recorded_as_rejected_not_platform_order(
    client, tokens, sync_engine
):
    """Rows that cannot become orders should be retained in rejected rows."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-BAD-1,,13800138000,商品甲,1,88.50"
    )

    body = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "invalid_youzan.csv")
    assert body["platform"] == "youzan"
    assert body["inserted_orders"] == 0
    assert body["invalid_rows"] == 1

    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 0
    assert _scalar(sync_engine, "SELECT count(*) FROM youzan_orders") == 0
    assert _scalar(sync_engine, "SELECT count(*) FROM upload_rejected_rows") == 1

    rejected = _row(
        sync_engine,
        """
        SELECT platform, source_row_number, reason, raw_payload
        FROM upload_rejected_rows
        """,
    )
    assert rejected["platform"] == "youzan"
    assert rejected["source_row_number"] == 1
    assert "order_date" in rejected["reason"] or "买家付款时间" in rejected["reason"]
    assert rejected["raw_payload"]["订单号"] == "YZ-BAD-1"


def test_jd_keeps_source_rows_but_maps_them_to_one_normalized_order(
    client, tokens, sync_engine
):
    """JD platform rows keep row granularity while analytics keeps one order."""
    csv = (
        "订单号,商品ID,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        "JD-MULTI-1,SKU-A,商品甲,1,2026-01-15 10:00:00,500.00,冯颖,"
        "广东深圳市福田区农园路66号,1******6198,250.00\n"
        "JD-MULTI-1,SKU-B,商品乙,2,2026-01-15 10:00:00,500.00,冯颖,"
        "广东深圳市福田区农园路66号,1******6198,250.00"
    )

    body = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "jd_multi.csv")
    assert body["platform"] == "jd"
    assert body["inserted_orders"] == 1
    assert body["raw_rows_inserted"] == 2
    assert body["duplicate_rows"] == 0

    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM jd_orders") == 2

    normalized_ids = _scalar(
        sync_engine,
        """
        SELECT count(DISTINCT normalized_order_id)
        FROM jd_orders
        WHERE "订单号" = 'JD-MULTI-1'
        """,
    )
    assert normalized_ids == 1

    order = _row(
        sync_engine,
        """
        SELECT sku, quantity, price
        FROM orders
        WHERE order_id = 'JD-MULTI-1'
        """,
    )
    assert order["sku"] == "商品甲、商品乙"
    assert order["quantity"] == 3
    assert float(order["price"]) == 500.00


def test_reuploading_jd_multiline_order_does_not_duplicate_platform_rows(
    client, tokens, sync_engine
):
    """JD row-level hashes should prevent duplicate platform rows on reupload."""
    csv = (
        "订单号,商品ID,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        "JD-DUP-MULTI,SKU-A,商品甲,1,2026-01-15 10:00:00,500.00,冯颖,"
        "广东深圳市福田区农园路66号,1******6198,250.00\n"
        "JD-DUP-MULTI,SKU-B,商品乙,2,2026-01-15 10:00:00,500.00,冯颖,"
        "广东深圳市福田区农园路66号,1******6198,250.00"
    )

    for filename in ("jd_multi_1.csv", "jd_multi_2.csv"):
        upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), filename)

    assert _scalar(sync_engine, "SELECT count(*) FROM upload_batches") == 2
    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM jd_orders") == 2


# ─── Helper ───────────────────────────────────────────────────────────────────


def _rows(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).mappings().all()


# ─── upload_batches record ─────────────────────────────────────────────────────


def test_upload_batch_record_tracks_correct_statistics(client, tokens, sync_engine):
    """Every upload creates exactly one upload_batches row; its counters must match
    the response body and the actual DB state."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-STAT-1,2025-07-21 10:00:00,13800138001,商品甲,1,88.50\n"
        "YZ-STAT-2,2025-07-21 11:00:00,13800138002,商品乙,2,176.00\n"
        "YZ-STAT-BAD,,13800138003,商品甲,1,88.50"
    )

    body = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "stats_test.csv")
    assert body["inserted_orders"] == 2
    assert body["invalid_rows"] == 1
    assert body["duplicate_rows"] == 0

    batch = _row(
        sync_engine,
        """
        SELECT platform, inserted_orders, duplicate_rows, invalid_rows, raw_rows_inserted
        FROM upload_batches
        """,
    )
    assert batch["platform"] == "youzan"
    assert batch["inserted_orders"] == 2
    assert batch["duplicate_rows"] == 0
    assert batch["invalid_rows"] == 1
    # For youzan each raw row = one order, so raw_rows_inserted == inserted_orders
    assert batch["raw_rows_inserted"] == 2

    assert _scalar(sync_engine, "SELECT count(*) FROM upload_batches") == 1


# ─── System metadata columns ───────────────────────────────────────────────────


def test_platform_row_system_metadata_is_fully_populated(client, tokens, sync_engine):
    """All system columns in a platform table row must be non-null after insert."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-META-1,2025-07-21 10:00:00,13800138000,商品甲,1,88.50"
    )

    client.post(
        "/upload/",
        files={"file": ("meta_test.csv", csv)},
        headers=_auth(tokens["admin"]),
    )

    row = _row(
        sync_engine,
        """
        SELECT batch_id, source_row_number, order_id, normalized_order_id,
               ingest_status, row_hash, created_at
        FROM youzan_orders
        WHERE "订单号" = 'YZ-META-1'
        """,
    )
    assert row["batch_id"] is not None
    assert row["source_row_number"] == 1
    assert row["order_id"] == "YZ-META-1"
    assert row["normalized_order_id"] is not None
    assert row["ingest_status"] == "inserted"
    assert row["row_hash"] is not None
    assert row["created_at"] is not None


# ─── source_row_number ─────────────────────────────────────────────────────────


def test_source_row_numbers_are_1_based_and_sequential(client, tokens, sync_engine):
    """source_row_number must reflect each row's 1-based position in the source file."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-SEQ-1,2025-07-21 10:00:00,13800138001,商品甲,1,88.50\n"
        "YZ-SEQ-2,2025-07-21 11:00:00,13800138002,商品乙,2,176.00\n"
        "YZ-SEQ-3,2025-07-21 12:00:00,13800138003,商品丙,1,60.00"
    )

    client.post(
        "/upload/",
        files={"file": ("seq_test.csv", csv)},
        headers=_auth(tokens["admin"]),
    )

    actual = {
        row["source_row_number"]
        for row in _rows(sync_engine, "SELECT source_row_number FROM youzan_orders")
    }
    assert actual == {1, 2, 3}


# ─── Mixed batch ───────────────────────────────────────────────────────────────


def test_mixed_batch_correctly_categorizes_valid_invalid_and_duplicate_rows(
    client, tokens, sync_engine
):
    """A single upload that contains new, duplicate, and invalid rows must record
    each category correctly without cross-contamination."""
    first_csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-MIX-A,2025-07-21 10:00:00,13800138001,商品甲,1,88.50\n"
        "YZ-MIX-B,2025-07-21 11:00:00,13800138002,商品乙,2,176.00"
    )
    client.post(
        "/upload/",
        files={"file": ("mix_first.csv", first_csv)},
        headers=_auth(tokens["admin"]),
    )

    # Second batch: 1 duplicate of MIX-A + 1 new MIX-C + 1 invalid (missing date)
    second_csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-MIX-A,2025-07-21 10:00:00,13800138001,商品甲,1,88.50\n"
        "YZ-MIX-C,2025-07-21 12:00:00,13800138003,商品丙,1,60.00\n"
        "YZ-MIX-BAD,,13800138004,商品甲,1,88.50"
    )
    body = upload_and_poll(
        client, _auth(tokens["admin"]), second_csv.encode(), "mix_second.csv"
    )
    assert body["inserted_orders"] == 1
    assert body["duplicate_rows"] == 1
    assert body["invalid_rows"] == 1

    # Total DB state after both uploads
    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 3          # A + B + C
    assert _scalar(sync_engine, "SELECT count(*) FROM youzan_orders") == 3   # A + B + C; dup/invalid excluded
    assert _scalar(sync_engine, "SELECT count(*) FROM upload_rejected_rows") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM upload_batches") == 2


# ─── Tmall platform ────────────────────────────────────────────────────────────


def test_tmall_upload_preserves_raw_platform_row(client, tokens, sync_engine):
    """Tmall uploads must write the original row (including unmapped fields) to
    tmall_orders and the normalised order to orders."""
    # 收货地址 uses Chinese commas so CSV parsing won't split the field.
    # Format: 姓名，86-手机号，地址
    csv = (
        "订单编号,支付单号,买家应付货款,总金额,订单状态,收货地址,订单创建时间,商品标题\n"
        "TM-RAW-1,PAY-001,288.00,288.00,交易成功,"
        "张三，86-13900139000，广东省深圳市福田区某街道123号,"
        "2025-07-21 10:00:00,商品甲"
    )

    body = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "tmall_raw.csv")
    assert body["platform"] == "tmall"
    assert body["inserted_orders"] == 1
    assert body["duplicate_rows"] == 0
    assert body["invalid_rows"] == 0

    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM tmall_orders") == 1

    row = _row(
        sync_engine,
        """
        SELECT "订单状态", "支付单号", ingest_status, normalized_order_id
        FROM tmall_orders
        WHERE "订单编号" = 'TM-RAW-1'
        """,
    )
    # Fields that are not mapped to orders columns must survive in the platform table
    assert row["订单状态"] == "交易成功"
    assert row["支付单号"] == "PAY-001"
    assert row["ingest_status"] == "inserted"
    assert row["normalized_order_id"] is not None


def test_tmall_duplicate_order_is_not_reinserted(client, tokens, sync_engine):
    """Re-uploading the same Tmall CSV should not duplicate orders or tmall_orders rows."""
    csv = (
        "订单编号,支付单号,买家应付货款,总金额,订单状态,收货地址,订单创建时间,商品标题\n"
        "TM-DUP-1,PAY-002,288.00,288.00,交易成功,"
        "李四，86-13700137000，北京市朝阳区建国路88号,"
        "2025-07-22 09:00:00,商品乙"
    )

    first = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "tm_dup_1.csv")
    second = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "tm_dup_2.csv")
    assert first["inserted_orders"] == 1
    assert second["inserted_orders"] == 0
    assert second["duplicate_rows"] == 1

    assert _scalar(sync_engine, "SELECT count(*) FROM upload_batches") == 2
    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM tmall_orders") == 1


# ─── normalized_order_id linkage ───────────────────────────────────────────────


def test_normalized_order_id_matches_orders_order_id_string(client, tokens, sync_engine):
    """normalized_order_id must store the same string as orders.order_id so that
    a direct join is possible without a surrogate-key lookup."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-LINK-1,2025-07-21 10:00:00,13800138000,商品甲,1,88.50"
    )
    client.post(
        "/upload/",
        files={"file": ("link_test.csv", csv)},
        headers=_auth(tokens["admin"]),
    )

    match_count = _scalar(
        sync_engine,
        """
        SELECT count(*)
        FROM youzan_orders y
        JOIN orders o ON o.order_id = y.normalized_order_id
        WHERE y."订单号" = 'YZ-LINK-1'
        """,
    )
    assert match_count == 1


def test_jd_normalized_order_id_joins_to_aggregated_order(client, tokens, sync_engine):
    """All jd_orders rows for a multi-product order must join to the single
    aggregated orders row via normalized_order_id."""
    csv = (
        "订单号,商品ID,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        "JD-JOIN-1,SKU-A,商品甲,1,2026-02-10 09:00:00,600.00,王芳,"
        "上海市徐汇区漕溪北路333号,1******0001,300.00\n"
        "JD-JOIN-1,SKU-B,商品丙,2,2026-02-10 09:00:00,600.00,王芳,"
        "上海市徐汇区漕溪北路333号,1******0001,300.00"
    )
    client.post(
        "/upload/",
        files={"file": ("jd_join.csv", csv)},
        headers=_auth(tokens["admin"]),
    )

    # Both raw rows must join to exactly one orders row
    joined_orders = _scalar(
        sync_engine,
        """
        SELECT count(DISTINCT o.id)
        FROM jd_orders j
        JOIN orders o ON o.order_id = j.normalized_order_id
        WHERE j."订单号" = 'JD-JOIN-1'
        """,
    )
    assert joined_orders == 1


# ─── batch_id FK linkage ───────────────────────────────────────────────────────


def test_batch_id_links_platform_rows_to_their_upload_batch(client, tokens, sync_engine):
    """Each platform row's batch_id must reference the upload_batches record for
    the specific upload that created it — not a prior or future batch."""
    for i, order_id in enumerate(["YZ-BLINK-1", "YZ-BLINK-2"], start=1):
        csv = (
            "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
            f"{order_id},2025-07-2{i} 10:00:00,1380013800{i},商品甲,1,88.50"
        )
        client.post(
            "/upload/",
            files={"file": (f"batch_{i}.csv", csv)},
            headers=_auth(tokens["admin"]),
        )

    # Two distinct batch_ids in youzan_orders
    rows = _rows(sync_engine, "SELECT DISTINCT batch_id FROM youzan_orders ORDER BY batch_id")
    distinct_batch_ids = [row["batch_id"] for row in rows]
    assert len(distinct_batch_ids) == 2
    assert distinct_batch_ids[0] != distinct_batch_ids[1]

    # Each batch_id references a real upload_batches row
    for bid in distinct_batch_ids:
        count = _scalar(
            sync_engine,
            "SELECT count(*) FROM upload_batches WHERE id = :bid",
            bid=bid,
        )
        assert count == 1


# ─── JD duplicate reupload response counts ────────────────────────────────────


def test_jd_duplicate_reupload_response_counts_are_accurate(client, tokens, sync_engine):
    """Reuploading a JD order whose row hashes already exist must report
    duplicate_rows equal to the number of raw source rows, not the order count."""
    csv = (
        "订单号,商品ID,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        "JD-REUP-1,SKU-A,商品甲,1,2026-03-01 08:00:00,400.00,陈静,"
        "浙江省杭州市西湖区文三路66号,1******0002,200.00\n"
        "JD-REUP-1,SKU-B,商品乙,1,2026-03-01 08:00:00,400.00,陈静,"
        "浙江省杭州市西湖区文三路66号,1******0002,200.00"
    )

    first = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "jd_reup_1.csv")
    assert first["inserted_orders"] == 1
    assert first["raw_rows_inserted"] == 2
    assert first["duplicate_rows"] == 0

    second = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "jd_reup_2.csv")
    assert second["inserted_orders"] == 0
    assert second["raw_rows_inserted"] == 0
    # Both raw rows were duplicates by row_hash — report 2, not 1
    assert second["duplicate_rows"] == 2


# ─── Real example-file uploads ────────────────────────────────────────────────


# ─── Intra-batch deduplication ────────────────────────────────────────────────


def test_intra_batch_duplicate_order_id_yields_one_order_and_one_platform_row(
    client, tokens, sync_engine
):
    """When the same order_id appears multiple times inside a single uploaded
    file, only the first occurrence creates an order and platform row; every
    subsequent occurrence is counted as duplicate_rows.  This applies to all
    three platforms via the shared in-batch deduplication set."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "YZ-INTRA-1,2025-07-21 10:00:00,13800138001,商品甲,1,88.50\n"
        "YZ-INTRA-1,2025-07-21 10:00:00,13800138001,商品乙,2,176.00\n"  # same order_id
        "YZ-INTRA-2,2025-07-21 11:00:00,13800138002,商品丙,1,60.00"
    )

    body = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "intra_dup.csv")
    assert body["inserted_orders"] == 2    # YZ-INTRA-1 (first) and YZ-INTRA-2
    assert body["duplicate_rows"] == 1     # second occurrence of YZ-INTRA-1
    assert body["invalid_rows"] == 0

    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 2
    assert _scalar(sync_engine, "SELECT count(*) FROM youzan_orders") == 2
    assert _scalar(sync_engine, "SELECT count(*) FROM upload_batches") == 1


def test_jd_intra_batch_same_order_id_rows_are_aggregated_not_duplicated(
    client, tokens, sync_engine
):
    """JD files legitimately contain multiple rows per order_id (one per SKU line).
    These are NOT duplicates — they are aggregated into one order with all SKUs
    and their source rows all land in jd_orders."""
    csv = (
        "订单号,商品ID,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        "JD-INTRA-1,SKU-A,商品甲,1,2026-04-01 10:00:00,750.00,孙磊,"
        "四川省成都市武侯区人民南路100号,1******0005,250.00\n"
        "JD-INTRA-1,SKU-B,商品乙,2,2026-04-01 10:00:00,750.00,孙磊,"
        "四川省成都市武侯区人民南路100号,1******0005,250.00\n"
        "JD-INTRA-1,SKU-C,商品丙,1,2026-04-01 10:00:00,750.00,孙磊,"
        "四川省成都市武侯区人民南路100号,1******0005,250.00"
    )

    body = upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "jd_intra.csv")
    assert body["platform"] == "jd"
    assert body["inserted_orders"] == 1    # one aggregated order
    assert body["raw_rows_inserted"] == 3  # all three SKU lines preserved
    assert body["duplicate_rows"] == 0     # none are duplicates

    assert _scalar(sync_engine, "SELECT count(*) FROM orders") == 1
    assert _scalar(sync_engine, "SELECT count(*) FROM jd_orders") == 3

    order = _row(
        sync_engine,
        "SELECT sku, quantity FROM orders WHERE order_id = 'JD-INTRA-1'",
    )
    assert "商品甲" in order["sku"]
    assert "商品乙" in order["sku"]
    assert "商品丙" in order["sku"]
    assert order["quantity"] == 4  # 1 + 2 + 1


# ─── Column value preservation ────────────────────────────────────────────────


def test_all_original_youzan_columns_preserved_with_correct_values(
    client, tokens, sync_engine
):
    """Platform tables store the original source-file string value for every
    provided column — both columns that are mapped to the normalized orders table
    and those that are not."""
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,"
        "订单实付金额,收货人/提货人,收货人省份,收货人地区,详细收货地址/提货地址,"
        "买家昵称,优惠券码名称,分销员,"
        "订单状态,买家备注,商家订单备注,归属店铺,运费\n"
        "YZ-COLS-1,2025-08-01 09:00:00,13900139000,商品甲,2,"
        "177.00,王小明,广东省,天河区,广东省广州市天河区花城大道100号,"
        "小明昵称,九折券,张导购员,"
        "交易成功,请放快递柜,VIP确认,官方旗舰店,0.00"
    )

    client.post(
        "/upload/",
        files={"file": ("yz_cols.csv", csv)},
        headers=_auth(tokens["admin"]),
    )

    row = _row(
        sync_engine,
        """
        SELECT
            "订单号", "买家付款时间", "收货人手机号/提货人手机号",
            "全部商品名称", "商品种类数", "订单实付金额",
            "收货人/提货人", "收货人省份", "收货人地区",
            "详细收货地址/提货地址", "买家昵称", "优惠券码名称", "分销员",
            "订单状态", "买家备注", "商家订单备注", "归属店铺", "运费"
        FROM youzan_orders
        WHERE "订单号" = 'YZ-COLS-1'
        """,
    )
    # Columns that also appear in the orders table (mapped)
    assert row["订单号"] == "YZ-COLS-1"
    assert row["收货人手机号/提货人手机号"] == "13900139000"
    assert row["全部商品名称"] == "商品甲"
    assert row["商品种类数"] == "2"
    assert row["订单实付金额"] == "177.00"
    assert row["收货人/提货人"] == "王小明"
    assert row["收货人省份"] == "广东省"
    assert row["收货人地区"] == "天河区"
    assert row["详细收货地址/提货地址"] == "广东省广州市天河区花城大道100号"
    assert row["买家昵称"] == "小明昵称"
    assert row["优惠券码名称"] == "九折券"
    assert row["分销员"] == "张导购员"
    # Columns not mapped to orders — only queryable via youzan_orders
    assert row["订单状态"] == "交易成功"
    assert row["买家备注"] == "请放快递柜"
    assert row["商家订单备注"] == "VIP确认"
    assert row["归属店铺"] == "官方旗舰店"
    assert row["运费"] == "0.00"


def test_all_original_tmall_columns_preserved_with_correct_values(
    client, tokens, sync_engine
):
    """All 8 Tmall source columns are stored verbatim in tmall_orders, including
    the composite 收货地址 field that is parsed during normalisation but must
    remain intact in the raw platform table."""
    csv = (
        "订单编号,支付单号,买家应付货款,总金额,订单状态,收货地址,订单创建时间,商品标题\n"
        "TM-COLS-1,PAY-C001,299.00,299.00,交易成功,"
        "赵雷，86-13600136000，上海市浦东新区世纪大道100号,"
        "2025-09-01 14:00:00,商品乙"
    )

    client.post(
        "/upload/",
        files={"file": ("tmall_cols.csv", csv)},
        headers=_auth(tokens["admin"]),
    )

    row = _row(
        sync_engine,
        """
        SELECT "订单编号", "支付单号", "买家应付货款", "总金额",
               "订单状态", "收货地址", "订单创建时间", "商品标题"
        FROM tmall_orders
        WHERE "订单编号" = 'TM-COLS-1'
        """,
    )
    assert row["订单编号"] == "TM-COLS-1"
    assert row["支付单号"] == "PAY-C001"
    assert row["买家应付货款"] == "299.00"
    assert row["总金额"] == "299.00"
    assert row["订单状态"] == "交易成功"
    # 收货地址 stored as original composite string — not the parsed components
    assert row["收货地址"] == "赵雷，86-13600136000，上海市浦东新区世纪大道100号"
    assert "2025-09-01" in str(row["订单创建时间"])
    assert row["商品标题"] == "商品乙"


def test_all_original_jd_columns_preserved_with_correct_values(
    client, tokens, sync_engine
):
    """JD platform-specific columns (京东价, 订单备注, 商家备注, etc.) that are
    not mapped to the normalized orders table must be queryable from jd_orders."""
    csv = (
        "订单号,商品ID,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,"
        "京东价,订单状态,订单备注,商家备注,支付方式\n"
        "JD-COLS-1,SKU-X,商品甲,1,2026-05-01 08:00:00,300.00,周华,"
        "北京市海淀区中关村大街1号,1******0009,"
        "300.00,已完成,客户要求工作日送货,内部备注:VIP,微信支付"
    )

    client.post(
        "/upload/",
        files={"file": ("jd_cols.csv", csv)},
        headers=_auth(tokens["admin"]),
    )

    row = _row(
        sync_engine,
        """
        SELECT "订单号", "商品ID", "商品名称", "订购数量",
               "京东价", "订单状态", "订单备注", "商家备注", "支付方式"
        FROM jd_orders
        WHERE "订单号" = 'JD-COLS-1'
        """,
    )
    # Mapped columns (go to orders after aggregation)
    assert row["订单号"] == "JD-COLS-1"
    assert row["商品名称"] == "商品甲"
    assert row["订购数量"] == "1"
    # Unmapped JD-specific columns
    assert row["商品ID"] == "SKU-X"
    assert row["京东价"] == "300.00"
    assert row["订单状态"] == "已完成"
    assert row["订单备注"] == "客户要求工作日送货"
    assert row["商家备注"] == "内部备注:VIP"
    assert row["支付方式"] == "微信支付"


def test_order_raw_endpoint_returns_youzan_platform_row(client, tokens):
    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,"
        "订单实付金额,订单状态,买家备注\n"
        "YZ-API-RAW-1,2025-07-21 10:00:00,13800138000,商品甲,1,88.50,"
        "交易成功,前端查询验证"
    )
    upload = client.post(
        "/upload/",
        files={"file": ("api_raw_youzan.csv", csv)},
        headers=_auth(tokens["admin"]),
    )
    assert upload.status_code == 202, upload.text

    orders = client.get("/orders_all/", headers=_auth(tokens["admin"])).json()
    order_id = next(row["id"] for row in orders if row["order_id"] == "YZ-API-RAW-1")

    raw = client.get(f"/orders_all/{order_id}/raw", headers=_auth(tokens["admin"]))
    assert raw.status_code == 200, raw.text
    body = raw.json()
    assert body["order"]["platform"] == "youzan"
    assert body["order"]["order_id"] == "YZ-API-RAW-1"
    assert body["row_count"] == 1
    assert body["rows"][0]["订单状态"] == "交易成功"
    assert body["rows"][0]["买家备注"] == "前端查询验证"


def test_order_raw_endpoint_returns_all_jd_rows_for_aggregated_order(client, tokens):
    csv = (
        "订单号,商品ID,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        "JD-API-RAW-1,SKU-A,商品甲,1,2026-02-10 09:00:00,600.00,王芳,"
        "上海市徐汇区漕溪北路333号,1******0001,300.00\n"
        "JD-API-RAW-1,SKU-B,商品丙,2,2026-02-10 09:00:00,600.00,王芳,"
        "上海市徐汇区漕溪北路333号,1******0001,300.00"
    )
    upload = client.post(
        "/upload/",
        files={"file": ("api_raw_jd.csv", csv)},
        headers=_auth(tokens["admin"]),
    )
    assert upload.status_code == 202, upload.text

    orders = client.get("/orders_all/", headers=_auth(tokens["admin"])).json()
    order_id = next(row["id"] for row in orders if row["order_id"] == "JD-API-RAW-1")

    raw = client.get(f"/orders_all/{order_id}/raw", headers=_auth(tokens["admin"]))
    assert raw.status_code == 200, raw.text
    body = raw.json()
    assert body["order"]["platform"] == "jd"
    assert body["row_count"] == 2
    assert {row["商品ID"] for row in body["rows"]} == {"SKU-A", "SKU-B"}

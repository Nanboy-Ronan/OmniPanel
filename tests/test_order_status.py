"""Order status and refunds: classification, ingestion, re-upload, revenue rule, backfill."""

import asyncio
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from test_api_endpoints import client, tokens  # noqa: F401

from app.db.order_status import (
    CLOSED, COMPLETED, COUNTED_GROUPS, DELETED, UNKNOWN, UNPAID, classify, parse_refund,
)
from app.utils.cache import analysis_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    asyncio.run(analysis_cache.invalidate())
    yield
    asyncio.run(analysis_cache.invalidate())


_HEADER = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,"
    "订单实付金额,订单状态,订单已退款金额"
)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload(client, token, rows, filename="status.csv"):
    r = client.post(
        "/upload/", files={"file": (filename, "\n".join([_HEADER, *rows]))}, headers=_auth(token)
    )
    assert r.status_code == 202, r.text


def _dashboard(client, token):
    r = client.get(
        "/analysis/dashboard",
        params={"start_date": "2025-07-01", "end_date": "2025-07-31"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    asyncio.run(analysis_cache.invalidate())
    return r.json()


class TestClassify:
    @pytest.mark.parametrize(
        "platform, raw, expected",
        [
            ("youzan", "交易完成", COMPLETED),
            ("youzan", "待发货", COMPLETED),
            ("youzan", "交易关闭", CLOSED),
            ("youzan", "待付款", UNPAID),
            ("jd", "等待出库", COMPLETED),
            ("jd", "完成", COMPLETED),
            ("jd", "已完成", COMPLETED),  # collector vocabulary
            ("jd", "已取消", CLOSED),
            ("jd", "锁定订单", CLOSED),
            ("jd", "(删除)等待出库", DELETED),
            ("jd", "(删除)暂停", DELETED),
            ("jd", "暂停订单", UNKNOWN),
            ("tmall", "交易成功", COMPLETED),
            ("tmall", "交易关闭", CLOSED),
            ("tmall", "等待买家付款", UNPAID),
            ("tmall", "", UNKNOWN),
            ("youzan", None, UNKNOWN),
            ("youzan", "某个新状态", UNKNOWN),
        ],
    )
    def test_mapping(self, platform, raw, expected):
        assert classify(platform, raw) == expected

    def test_counted_groups(self):
        # JD "(删除)" orders are excluded from revenue (decided 2026-10-10).
        assert set(COUNTED_GROUPS) == {COMPLETED, UNKNOWN}

    def test_deleted_prefix_is_jd_only(self):
        assert classify("youzan", "(删除)交易完成") == UNKNOWN

    @pytest.mark.parametrize(
        "value, expected",
        [("12.50", Decimal("12.50")), (12.5, Decimal("12.50")), ("1,200.00", Decimal("1200.00")),
         ("0.00", None), ("", None), (None, None), ("nan", None), ("abc", None)],
    )
    def test_parse_refund(self, value, expected):
        assert parse_refund(value) == expected


class TestRevenueRule:
    def test_closed_orders_excluded_and_refunds_netted(self, client, tokens):
        _upload(client, tokens["admin"], [
            "A1,2025-07-02,13800000001,item,1,100,交易完成,",
            "A2,2025-07-03,13800000002,item,1,50,交易关闭,",
            "A3,2025-07-04,13800000003,item,1,80,交易完成,30.00",
        ])
        data = _dashboard(client, tokens["analyst"])
        assert data["current"]["orders"] == 2
        assert data["current"]["revenue"] == pytest.approx(150.0)  # 100 + (80 - 30)
        assert data["current"]["customers"] == 2
        assert data["status"] == {
            "excluded_orders": 1, "excluded_amount": 50.0, "refunds": 30.0, "unknown_orders": 0,
            "deleted_orders": 0, "deleted_amount": 0.0,
        }

        kpi = client.get(
            "/analysis/kpi-periods", params={"anchor": "2025-07-04"}, headers=_auth(tokens["analyst"])
        ).json()
        assert kpi["month"]["orders"] == 2
        assert kpi["month"]["revenue"] == pytest.approx(150.0)

    def test_customer_history_keeps_closed_orders_but_not_their_spend(self, client, tokens):
        _upload(client, tokens["admin"], [
            "B1,2025-07-02,13800000009,item,1,100,交易完成,10",
            "B2,2025-07-05,13800000009,item,1,60,交易关闭,",
        ])
        r = client.get("/analysis/customers/13800000009", headers=_auth(tokens["analyst"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 2
        assert body["total_spend"] == pytest.approx(90.0)
        assert {o["status_group"] for o in body["orders"]} == {"已成交", "已关闭"}

    def test_reupload_updates_status_only(self, client, tokens):
        _upload(client, tokens["admin"], ["C1,2025-07-02,13800000005,item,1,100,待发货,"])
        assert _dashboard(client, tokens["analyst"])["current"]["orders"] == 1

        _upload(client, tokens["admin"], ["C1,2025-07-02,13800000005,item,1,999,交易关闭,"], "again.csv")
        data = _dashboard(client, tokens["analyst"])
        assert data["current"]["orders"] == 0
        assert data["status"]["excluded_orders"] == 1
        assert data["status"]["excluded_amount"] == 100.0  # amount keeps the first upload

        rows = client.get("/orders_all/", params={"q": "C1"}, headers=_auth(tokens["admin"])).json()
        assert rows[0]["raw_status"] == "交易关闭"

    def test_reupload_without_status_column_keeps_status(self, client, tokens):
        _upload(client, tokens["admin"], ["D1,2025-07-02,13800000006,item,1,100,交易关闭,"])
        header = "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额"
        r = client.post(
            "/upload/",
            files={"file": ("plain.csv", f"{header}\nD1,2025-07-02,13800000006,item,1,100")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 202
        assert _dashboard(client, tokens["analyst"])["status"]["excluded_orders"] == 1


def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0022_order_status.py"
    spec = importlib.util.spec_from_file_location("migration_0022", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_from_raw_tables(client, tokens, pg_sync_url):
    _upload(client, tokens["admin"], [
        "E1,2025-07-02,13800000007,item,1,100,交易完成,",
        "E2,2025-07-03,13800000008,item,1,50,交易关闭,",
        "E3,2025-07-04,13800000010,item,1,80,交易完成,25.5",
    ])
    engine = create_engine(pg_sync_url)
    try:
        with engine.begin() as conn:
            # Simulate orders ingested before statuses were stored.
            conn.execute(text(
                "UPDATE orders SET raw_status = NULL, status_group = 'unknown', refunded_amount = NULL"
            ))
            _load_migration()._backfill(conn)
            rows = dict(
                (r.order_id, (r.raw_status, r.status_group, r.refunded_amount))
                for r in conn.execute(text("SELECT order_id, raw_status, status_group, refunded_amount FROM orders"))
            )
    finally:
        engine.dispose()
    assert rows == {
        "E1": ("交易完成", "completed", None),
        "E2": ("交易关闭", "closed", None),
        "E3": ("交易完成", "completed", Decimal("25.50")),
    }

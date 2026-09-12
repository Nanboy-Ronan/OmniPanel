from __future__ import annotations

import io

import pandas as pd
import pytest

from app.collector.jd import _looks_like_jd_login, csv_bytes_from_card_snapshots, rows_from_card_snapshots
from app.collector.errors import EmptyExportError
from app.db.etl import normalize_dataframe


def _cards() -> list[dict]:
    return [{
        "orderId": "3606276015319073",
        "orderTime": "2026-08-30 00:10:44",
        "amount": "1500.00",
        "combinedAddress": "朱**，贵州贵阳市云岩区**********",
        "phone": "1******9690",
        "userPin": "jd_4ebd84b8c53b7",
        "statusText": "待付款\n05时21分后订单取消",
        "skus": [
            {"name": "商品甲", "skuId": "101", "price": "1453.32", "quantity": 1},
            {"name": "赠品", "skuId": "102", "price": "46.68", "quantity": 2},
        ],
    }]


def test_card_snapshot_becomes_one_row_per_sku_with_masked_fields() -> None:
    rows = rows_from_card_snapshots(_cards())
    assert len(rows) == 2
    assert rows[0]["订单号"] == "3606276015319073"
    assert rows[0]["客户姓名"] == "朱**"
    assert rows[0]["客户地址"] == "贵州贵阳市云岩区**********"
    assert rows[0]["联系电话"] == "1******9690"
    assert rows[0]["订单状态"] == "待付款"
    assert rows[1]["订购数量"] == 2


def test_collector_csv_is_detected_as_jd_and_uses_user_pin_as_customer_key() -> None:
    raw = csv_bytes_from_card_snapshots(_cards())
    frame = pd.read_csv(io.BytesIO(raw), dtype=str)
    normalized, platform = normalize_dataframe(frame)
    assert platform == "jd"
    assert len(normalized) == 1
    assert normalized.iloc[0]["客户标识"] == "jd_4ebd84b8c53b7"
    assert normalized.iloc[0]["商品种类数"] == 3
    assert normalized.iloc[0]["全部商品名称"] == "商品甲、赠品"


def test_manual_jd_without_collector_key_still_uses_address() -> None:
    frame = pd.DataFrame([{
        "订单号": "1",
        "商品名称": "商品",
        "订购数量": "1",
        "下单时间": "2026-08-30 00:00:00",
        "京东价": "10",
        "订单金额": "10",
        "客户地址": "脱敏地址",
        "联系电话": "1******0000",
    }])
    normalized, _ = normalize_dataframe(frame)
    assert normalized.iloc[0]["客户标识"] == "脱敏地址"


def test_empty_snapshot_raises_distinct_empty_export_error() -> None:
    with pytest.raises(EmptyExportError):
        csv_bytes_from_card_snapshots([])


def test_login_title_is_detected_even_before_jd_redirects_the_url() -> None:
    class _Page:
        url = "https://shop.jd.com/jdm/home"

        def title(self):
            return "Loading https://passport.shop.jd.com/login/index.action/jdm"

        def inner_text(self, _selector):
            return ""

    assert _looks_like_jd_login(_Page())

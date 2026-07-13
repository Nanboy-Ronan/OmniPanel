"""
Unit tests for multi-platform ETL support (JD & Tmall).

Tests are designed to be self-contained — they use inline DataFrame fixtures
so they can run without the actual example files being present.

Run with:
    pytest tests/test_multiplatform.py -v
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from datetime import date

from conftest import upload_and_poll


# ---------------------------------------------------------------------------
# Fixtures — minimal DataFrames that mimic the real exports
# ---------------------------------------------------------------------------

@pytest.fixture
def youzan_df():
    """Minimal Youzan CSV row."""
    return pd.DataFrame([
        {
            "订单号": "YZ-EX-0001",
            "买家付款时间": "2025-07-20 16:42:54",
            "订单实付金额": "539.10",
            "全部商品名称": "商品甲2盒尊享礼品装",
            "商品种类数": "1",
            "收货人/提货人": "王芳",
            "收货人手机号/提货人手机号": "13900000001",
            "收货人省份": "山东省",
            "收货人城市": "济南市",
            "收货人地区": "历城区",
            "详细收货地址/提货地址": "山东省济南市历城区示例街道1号",
            "买家昵称": "昵称甲",
            "优惠券码名称": "9折优惠券",
            "分销员": "导购甲",
        },
        {
            "订单号": "YZ-EX-0002",
            "买家付款时间": "2025-07-20 16:00:42",
            "订单实付金额": "539.10",
            "全部商品名称": "商品甲2盒尊享礼品装",
            "商品种类数": "1",
            "收货人/提货人": "刘洋",
            "收货人手机号/提货人手机号": "13900000002",
            "收货人省份": "广东省",
            "收货人城市": "湛江市",
            "收货人地区": "徐闻县",
            "详细收货地址/提货地址": "广东省湛江市徐闻县示例街道1号",
            "买家昵称": "昵称乙",
            "优惠券码名称": "9折优惠券",
            "分销员": "导购甲",
        },
    ], dtype=str)


@pytest.fixture
def jd_df():
    """Minimal JD XLSX rows (two orders, same customer address = same customer)."""
    return pd.DataFrame([
        {
            "订单号": "JD-EX-0001",
            "商品名称": "商品乙 60粒*1瓶",
            "订购数量": "1",
            "下单时间": "2026-01-31 08:06:59",
            "订单金额": "541.59",
            "商家应收": "499.00（系统计算中，该值仅供参考）",
            "订单状态": "完成",
            "客户姓名": "陈静",
            "客户地址": "广东深圳市福田区示例街道1号",
            "联系电话": "1******6198",
            "京东价": "541.59",
        },
        {
            # Different order, SAME address → should be same customer
            "订单号": "JD-EX-0002",
            "商品名称": "商品乙 60粒*1瓶",
            "订购数量": "1",
            "下单时间": "2026-01-28 21:04:42",
            "订单金额": "499.00",
            "商家应收": "499.00（系统计算中，该值仅供参考）",
            "订单状态": "完成",
            "客户姓名": "陈静",
            "客户地址": "广东深圳市福田区示例街道1号",
            "联系电话": "1******6198",
            "京东价": "499.00",
        },
        {
            # Third order, different address → different customer
            "订单号": "JD-EX-0003",
            "商品名称": "商品乙随身装 20粒*1袋",
            "订购数量": "1",
            "下单时间": "2026-01-28 10:06:06",
            "订单金额": "168.00",
            "商家应收": "168.00（系统计算中，该值仅供参考）",
            "订单状态": "完成",
            "客户姓名": "赵磊",
            "客户地址": "内蒙古巴彦淖尔市临河区示例街道1号",
            "联系电话": "1******9993",
            "京东价": "168.00",
        },
    ], dtype=str)


@pytest.fixture
def tmall_df():
    """Minimal Tmall XLSX rows."""
    return pd.DataFrame([
        {
            "订单编号": "TM-EX-0001",
            "支付单号": "",
            "买家应付货款": "",
            "总金额": "520",
            "订单状态": "",
            "收货地址": "周强，86-13900000004，江苏省 扬州市 广陵区 示例地址 ",
            "订单创建时间": "2026-02-01 09:00:00",
            "商品标题": "商品丁",
        },
        {
            # Same customer, different order (same address after stripping)
            "订单编号": "TM-EX-0002",
            "支付单号": "",
            "买家应付货款": "",
            "总金额": "960",
            "订单状态": "",
            "收货地址": "孙强，86-13900000003，广东省 韶关市 乐昌市 示例地址 ，",
            "订单创建时间": "2026-02-05 14:00:00",
            "商品标题": "商品丁",
        },
    ], dtype=str)


# ===========================================================================
# 1. Platform Detection
# ===========================================================================

class TestDetectPlatform:
    """detect_platform(df) should return 'youzan', 'jd', or 'tmall'."""

    def test_detects_youzan(self, youzan_df):
        from app.db.etl import detect_platform
        assert detect_platform(youzan_df) == "youzan"

    def test_detects_jd(self, jd_df):
        from app.db.etl import detect_platform
        assert detect_platform(jd_df) == "jd"

    def test_detects_tmall(self, tmall_df):
        from app.db.etl import detect_platform
        assert detect_platform(tmall_df) == "tmall"

    def test_unknown_raises(self):
        from app.db.etl import detect_platform
        bad_df = pd.DataFrame([{"foo": "bar", "baz": "qux"}])
        with pytest.raises(ValueError, match="(?i)unknown|unsupported|unrecognised|unrecognized"):
            detect_platform(bad_df)


# ===========================================================================
# 2. Tmall Address Parsing
# ===========================================================================

class TestParseTmallAddress:
    """_parse_tmall_address(raw) → (name, phone, address)"""

    def test_standard_case(self):
        from app.db.etl import _parse_tmall_address
        name, phone, addr = _parse_tmall_address(
            "周强，86-13900000004，江苏省 扬州市 广陵区 示例地址 "
        )
        assert name == "周强"
        assert phone == "13900000004"
        assert "江苏省" in addr
        assert "示例地址" in addr

    def test_trailing_separator(self):
        from app.db.etl import _parse_tmall_address
        name, phone, addr = _parse_tmall_address(
            "孙强，86-13900000003，广东省 韶关市 乐昌市 示例地址 ，"
        )
        assert name == "孙强"
        assert phone == "13900000003"
        assert "广东省" in addr

    def test_strips_86_prefix(self):
        from app.db.etl import _parse_tmall_address
        _, phone, _ = _parse_tmall_address("王明，86-13800138000，北京市 朝阳区 某街道某号")
        assert phone == "13800138000"
        assert not phone.startswith("86")

    def test_no_86_prefix(self):
        """Phone without 86- prefix should still be extracted correctly."""
        from app.db.etl import _parse_tmall_address
        _, phone, _ = _parse_tmall_address("王明，13800138000，北京市 朝阳区 某街道某号")
        assert phone == "13800138000"

    def test_address_stripped(self):
        """Returned address should have no leading/trailing whitespace."""
        from app.db.etl import _parse_tmall_address
        _, _, addr = _parse_tmall_address(
            "周强，86-13900000004，  江苏省 扬州市 广陵区  "
        )
        assert addr == addr.strip()

    def test_empty_string_returns_none_tuple(self):
        from app.db.etl import _parse_tmall_address
        name, phone, addr = _parse_tmall_address("")
        assert name is None
        assert phone is None
        assert addr is None


# ===========================================================================
# 3. normalize_dataframe — Unified Schema
# ===========================================================================

REQUIRED_UNIFIED_COLS = [
    "订单号",
    "买家付款时间",
    "订单实付金额",
    "全部商品名称",
    "商品种类数",
    "客户标识",
    "平台",
    # address-related
    "收货人/提货人",
    "收货人手机号/提货人手机号",
    "详细收货地址/提货地址",
]


class TestNormalizeDataframeYouzan:
    def test_returns_dataframe_and_platform_string(self, youzan_df):
        from app.db.etl import normalize_dataframe
        result, platform = normalize_dataframe(youzan_df)
        assert isinstance(result, pd.DataFrame)
        assert platform == "youzan"

    def test_required_columns_present(self, youzan_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df)
        for col in REQUIRED_UNIFIED_COLS:
            assert col in result.columns, f"Missing column: {col}"

    def test_platform_column_values(self, youzan_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df)
        assert (result["平台"] == "youzan").all()

    def test_customer_key_is_phone(self, youzan_df):
        """For Youzan, customer_key should be the phone number."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df)
        assert result["客户标识"].iloc[0] == "13900000001"
        assert result["客户标识"].iloc[1] == "13900000002"

    def test_row_count_preserved(self, youzan_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df)
        assert len(result) == len(youzan_df)

    def test_order_id_mapped(self, youzan_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df)
        assert result["订单号"].iloc[0] == "YZ-EX-0001"

    def test_price_mapped(self, youzan_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df)
        assert result["订单实付金额"].iloc[0] == "539.10"


class TestNormalizeDataframeJD:
    def test_returns_dataframe_and_platform_string(self, jd_df):
        from app.db.etl import normalize_dataframe
        result, platform = normalize_dataframe(jd_df)
        assert isinstance(result, pd.DataFrame)
        assert platform == "jd"

    def test_required_columns_present(self, jd_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        for col in REQUIRED_UNIFIED_COLS:
            assert col in result.columns, f"Missing column for JD: {col}"

    def test_platform_column_values(self, jd_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        assert (result["平台"] == "jd").all()

    def test_customer_key_is_address(self, jd_df):
        """For JD, customer_key should be the normalised delivery address."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        key0 = result["客户标识"].iloc[0]
        key1 = result["客户标识"].iloc[1]
        # First two rows share the same address → same customer key
        assert key0 == key1, "Same address should map to same customer_key"
        # Third row has different address
        key2 = result["客户标识"].iloc[2]
        assert key0 != key2, "Different address should map to different customer_key"

    def test_customer_key_not_phone(self, jd_df):
        """JD phone numbers are masked — customer_key must NOT be the masked phone."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        key = result["客户标识"].iloc[0]
        # The masked phone is '1******6198', should not equal the key
        assert key != "1******6198"

    def test_order_id_mapped(self, jd_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        assert result["订单号"].iloc[0] == "JD-EX-0001"

    def test_date_mapped(self, jd_df):
        """下单时间 → 买家付款时间"""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        assert "2026-01-31" in result["买家付款时间"].iloc[0]

    def test_price_mapped(self, jd_df):
        """订单金额 → 订单实付金额 (strip any footnotes)."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        val = result["订单实付金额"].iloc[0]
        # Should be numeric-ish (no Chinese text in value)
        assert "541" in val, f"Unexpected price value: {val}"

    def test_sku_mapped(self, jd_df):
        """商品名称 → 全部商品名称"""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        assert "商品乙" in result["全部商品名称"].iloc[0]

    def test_receiver_name_mapped(self, jd_df):
        """客户姓名 → 收货人/提货人"""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        assert result["收货人/提货人"].iloc[0] == "陈静"

    def test_address_mapped(self, jd_df):
        """客户地址 → 详细收货地址/提货地址"""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        assert "深圳市" in result["详细收货地址/提货地址"].iloc[0]

    def test_row_count_preserved(self, jd_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(jd_df)
        assert len(result) == len(jd_df)


class TestNormalizeDataframeTmall:
    def test_returns_dataframe_and_platform_string(self, tmall_df):
        from app.db.etl import normalize_dataframe
        result, platform = normalize_dataframe(tmall_df)
        assert isinstance(result, pd.DataFrame)
        assert platform == "tmall"

    def test_required_columns_present(self, tmall_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        for col in REQUIRED_UNIFIED_COLS:
            assert col in result.columns, f"Missing column for Tmall: {col}"

    def test_platform_column_values(self, tmall_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        assert (result["平台"] == "tmall").all()

    def test_customer_key_is_parsed_address(self, tmall_df):
        """For Tmall, customer_key = parsed address portion (not full raw string)."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        key0 = result["客户标识"].iloc[0]
        # Must contain address text
        assert "江苏省" in key0 or "淮海路" in key0, f"Unexpected customer_key: {key0}"
        # Must NOT contain the phone number
        assert "13900000004" not in key0

    def test_two_customers_have_different_keys(self, tmall_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        assert result["客户标识"].iloc[0] != result["客户标识"].iloc[1]

    def test_receiver_name_parsed(self, tmall_df):
        """收货人/提货人 should be extracted from the composite address field."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        assert result["收货人/提货人"].iloc[0] == "周强"
        assert result["收货人/提货人"].iloc[1] == "孙强"

    def test_phone_parsed(self, tmall_df):
        """收货人手机号/提货人手机号 should strip 86- prefix."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        assert result["收货人手机号/提货人手机号"].iloc[0] == "13900000004"
        assert result["收货人手机号/提货人手机号"].iloc[1] == "13900000003"

    def test_address_parsed(self, tmall_df):
        """详细收货地址/提货地址 should be just the address portion."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        addr0 = result["详细收货地址/提货地址"].iloc[0]
        assert "江苏省" in addr0
        # Should not contain the name or phone
        assert "周强" not in addr0
        assert "13900000004" not in addr0

    def test_order_id_mapped(self, tmall_df):
        """订单编号 → 订单号"""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        assert result["订单号"].iloc[0] == "TM-EX-0001"

    def test_price_mapped(self, tmall_df):
        """总金额 → 订单实付金额"""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        assert result["订单实付金额"].iloc[0] == "520"

    def test_row_count_preserved(self, tmall_df):
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(tmall_df)
        assert len(result) == len(tmall_df)


# ===========================================================================
# 4. Cross-platform: Same Schema Shape
# ===========================================================================

class TestUnifiedSchema:
    """All three platforms must produce the same required column set."""

    @pytest.mark.parametrize("platform_name,fixture_name", [
        ("youzan", "youzan_df"),
        ("jd", "jd_df"),
        ("tmall", "tmall_df"),
    ])
    def test_all_platforms_have_required_cols(self, platform_name, fixture_name, request):
        from app.db.etl import normalize_dataframe
        df = request.getfixturevalue(fixture_name)
        result, detected = normalize_dataframe(df)
        assert detected == platform_name
        for col in REQUIRED_UNIFIED_COLS:
            assert col in result.columns, (
                f"Platform '{platform_name}' missing column '{col}'"
            )


# ===========================================================================
# 5. ingest() — Customer-key based deduplication
# ===========================================================================

class TestIngestCustomerKey:
    """After normalization, ingest() should use customer_key (not mobile) to
    look up/create Customer records."""

    def _make_session(self):
        """Return a minimal SQLAlchemy-like mock session."""
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None
        return session

    def test_ingest_jd_creates_customers_by_address(self, jd_df):
        """Two JD orders with the same address → one Customer record created."""
        from app.db.etl import normalize_dataframe, ingest
        norm_df, _ = normalize_dataframe(jd_df)

        session = self._make_session()
        # Track Customer objects added
        added = []
        session.add.side_effect = added.append
        session.flush = MagicMock()
        session.commit = MagicMock()

        ingest(norm_df, session)

        # Collect only Customer instances
        from app.db.models import Customer
        customer_adds = [o for o in added if isinstance(o, Customer)]
        # 2 distinct addresses in jd_df fixture → 2 customers
        unique_keys = {c.customer_key for c in customer_adds}
        assert len(unique_keys) == 2, (
            f"Expected 2 unique customer_keys, got {unique_keys}"
        )

    def test_ingest_tmall_creates_customers_by_address(self, tmall_df):
        """Two Tmall orders with different addresses → two Customer records."""
        from app.db.etl import normalize_dataframe, ingest
        norm_df, _ = normalize_dataframe(tmall_df)

        session = self._make_session()
        added = []
        session.add.side_effect = added.append
        session.flush = MagicMock()
        session.commit = MagicMock()

        ingest(norm_df, session)

        from app.db.models import Customer
        customer_adds = [o for o in added if isinstance(o, Customer)]
        unique_keys = {c.customer_key for c in customer_adds}
        assert len(unique_keys) == 2

    def test_ingest_jd_stores_platform(self, jd_df):
        """Ingested Order records should carry platform='jd'."""
        from app.db.etl import normalize_dataframe, ingest
        norm_df, _ = normalize_dataframe(jd_df)

        session = self._make_session()
        added = []
        session.add.side_effect = added.append
        session.flush = MagicMock()
        session.commit = MagicMock()

        ingest(norm_df, session)

        from app.db.models import Order
        order_adds = [o for o in added if isinstance(o, Order)]
        assert all(o.platform == "jd" for o in order_adds), (
            "All JD orders should have platform='jd'"
        )

    def test_ingest_tmall_stores_platform(self, tmall_df):
        """Ingested Order records should carry platform='tmall'."""
        from app.db.etl import normalize_dataframe, ingest
        norm_df, _ = normalize_dataframe(tmall_df)

        session = self._make_session()
        added = []
        session.add.side_effect = added.append
        session.flush = MagicMock()
        session.commit = MagicMock()

        ingest(norm_df, session)

        from app.db.models import Order
        order_adds = [o for o in added if isinstance(o, Order)]
        assert all(o.platform == "tmall" for o in order_adds)

    def test_ingest_returns_inserted_count(self, jd_df):
        from app.db.etl import normalize_dataframe, ingest
        norm_df, _ = normalize_dataframe(jd_df)

        session = self._make_session()
        session.flush = MagicMock()
        session.commit = MagicMock()

        count = ingest(norm_df, session)
        assert count == len(jd_df), (
            f"Expected {len(jd_df)} insertions, got {count}"
        )


# ===========================================================================
# 6. Real-file smoke tests (skipped if files not present)
# ===========================================================================

import os

YOUZAN_FILE = "data/youzan-example.csv"
JD_FILE = "data/jd-example.xlsx"
TMALL_FILE = "data/tmall-example.xlsx"


@pytest.mark.skipif(not os.path.exists(JD_FILE), reason="JD example file not found")
class TestRealJDFile:
    def test_detect_platform_real_file(self):
        from app.db.etl import detect_platform
        df = pd.read_excel(JD_FILE, dtype=str)
        assert detect_platform(df) == "jd"

    def test_normalize_real_file(self):
        from app.db.etl import normalize_dataframe
        df = pd.read_excel(JD_FILE, dtype=str)
        result, platform = normalize_dataframe(df)
        assert platform == "jd"
        assert len(result) > 0
        for col in REQUIRED_UNIFIED_COLS:
            assert col in result.columns, f"Missing: {col}"
        # customer_key must not be empty
        assert result["客户标识"].notna().any()


@pytest.mark.skipif(not os.path.exists(TMALL_FILE), reason="Tmall example file not found")
class TestRealTmallFile:
    def test_detect_platform_real_file(self):
        from app.db.etl import detect_platform
        df = pd.read_excel(TMALL_FILE, dtype=str)
        assert detect_platform(df) == "tmall"

    def test_normalize_real_file(self):
        from app.db.etl import normalize_dataframe
        df = pd.read_excel(TMALL_FILE, dtype=str)
        result, platform = normalize_dataframe(df)
        assert platform == "tmall"
        assert len(result) > 0
        for col in REQUIRED_UNIFIED_COLS:
            assert col in result.columns, f"Missing: {col}"
        assert result["收货人/提货人"].notna().any()
        assert result["收货人手机号/提货人手机号"].notna().any()


@pytest.mark.skipif(not os.path.exists(YOUZAN_FILE), reason="Youzan example file not found")
class TestRealYouzanFile:
    def test_detect_platform_real_file(self):
        from app.db.etl import detect_platform
        df = pd.read_csv(YOUZAN_FILE, dtype=str)
        assert detect_platform(df) == "youzan"

    def test_normalize_real_file(self):
        from app.db.etl import normalize_dataframe
        df = pd.read_csv(YOUZAN_FILE, dtype=str)
        result, platform = normalize_dataframe(df)
        assert platform == "youzan"
        assert len(result) > 0
        for col in REQUIRED_UNIFIED_COLS:
            assert col in result.columns, f"Missing: {col}"
        assert result["客户标识"].notna().any()


# ===========================================================================
# 7. API Integration Tests — Upload multi-platform data
# ===========================================================================

import importlib

try:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker as sa_sessionmaker
    from fastapi.testclient import TestClient
    HAS_API_DEPS = True
except ImportError:
    HAS_API_DEPS = False


def _make_csv(rows_str: str) -> bytes:
    """Helper: return CSV bytes from a multiline string."""
    return rows_str.strip().encode("utf-8")


def _make_jd_csv() -> str:
    """Minimal JD-style CSV with the columns normalize_dataframe expects."""
    return (
        "订单号,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        "JD001,商品乙,1,2026-01-15 10:00:00,500.00,陈静,广东深圳市福田区示例街道1号,1******6198,500.00\n"
        "JD002,商品乙,1,2026-01-20 12:00:00,500.00,陈静,广东深圳市福田区示例街道1号,1******6198,500.00\n"
        "JD003,商品甲,1,2026-01-25 08:00:00,300.00,赵磊,内蒙古巴彦淖尔市示例街道1号,1******9993,300.00"
    )


def _make_tmall_csv() -> str:
    """Minimal Tmall-style CSV with the columns normalize_dataframe expects."""
    return (
        "订单编号,总金额,收货地址,订单创建时间,商品标题\n"
        "TM001,520,周强，86-13900000004，江苏省 扬州市 广陵区 示例地址,2026-02-01 09:00:00,商品丁\n"
        "TM002,960,孙强，86-13900000003，广东省 韶关市 乐昌市 示例镇,2026-02-05 14:00:00,商品丁"
    )


def _make_youzan_csv() -> str:
    """Minimal Youzan-style CSV."""
    return (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额,"
        "收货人/提货人,收货人省份,收货人地区,详细收货地址/提货地址,买家昵称,优惠券码名称,分销员\n"
        "YZ001,2025-07-20 16:42:54,13900000001,商品甲,1,539.10,"
        "王芳,山东省,历城区,山东省济南市历城区王舍人街道,昵称甲,9折优惠券,导购甲\n"
        "YZ002,2025-07-20 16:00:42,13900000002,商品甲,1,539.10,"
        "刘洋,广东省,徐闻县,广东省湛江市徐闻县,昵称乙,9折优惠券,导购甲"
    )


@pytest.fixture
def api_client(pg_async_url, monkeypatch):
    """Create a FastAPI TestClient with a fresh temp database."""
    if not HAS_API_DEPS:
        pytest.skip("API dependencies not installed")

    from sqlalchemy import create_engine as _sync_engine
    from sqlalchemy.orm import Session as _SyncSession
    from sqlalchemy.pool import NullPool
    import app.db as db_mod

    test_engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    TestSessionLocal = sa_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Background ingestion runs in a thread on the *sync* session, so it must be
    # pointed at the same test database — otherwise it hits the real DB and the
    # tables don't exist.
    sync_url = pg_async_url.replace("+asyncpg", "+psycopg2")
    s_engine = _sync_engine(sync_url, poolclass=NullPool)
    TestSyncSessionLocal = sa_sessionmaker(s_engine, class_=_SyncSession, expire_on_commit=False)

    monkeypatch.setattr(db_mod, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db_mod, "engine", test_engine, raising=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", TestSessionLocal, raising=False)
    monkeypatch.setattr(db_mod, "SyncSessionLocal", TestSyncSessionLocal, raising=False)

    import app.main
    importlib.reload(app.main)

    with TestClient(app.main.app) as c:
        yield c
    import asyncio
    asyncio.run(test_engine.dispose())
    s_engine.dispose()


@pytest.fixture
def api_tokens(api_client):
    """Register admin + analyst users and return their tokens."""
    # First registration → auto-admin
    r_reg = api_client.post(
        "/auth/register",
        json={"email": "admin@mptest.com", "password": "pw", "role": "viewer"},
    )
    assert r_reg.status_code == 201, f"Registration failed: {r_reg.text}"

    r_login = api_client.post(
        "/auth/jwt/login",
        data={"username": "admin@mptest.com", "password": "pw"},
    )
    assert r_login.status_code == 200, f"Admin login failed: {r_login.text}"
    admin_token = r_login.json()["access_token"]

    # Create an analyst via admin
    r_create = api_client.post(
        "/admin/users",
        json={"email": "analyst@mptest.com", "password": "pw", "role": "analyst"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r_create.status_code == 201, f"Analyst creation failed: {r_create.text}"

    r_analyst = api_client.post(
        "/auth/jwt/login",
        data={"username": "analyst@mptest.com", "password": "pw"},
    )
    assert r_analyst.status_code == 200, f"Analyst login failed: {r_analyst.text}"
    analyst_token = r_analyst.json()["access_token"]

    return {"admin": admin_token, "analyst": analyst_token}



def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestUploadJD:
    """Uploading JD data via /upload/ should succeed and be queryable."""

    def test_upload_jd_csv_returns_202(self, api_client, api_tokens):
        csv = _make_jd_csv()
        data = upload_and_poll(api_client, _auth(api_tokens["admin"]), csv, "jd_orders.csv")
        assert data["inserted_rows"] >= 1

    def test_upload_jd_returns_platform_name(self, api_client, api_tokens):
        """Batch record should indicate detected platform."""
        csv = _make_jd_csv()
        data = upload_and_poll(api_client, _auth(api_tokens["admin"]), csv, "jd_orders.csv")
        assert data.get("platform") == "jd", (
            f"Expected platform='jd' in batch, got: {data}"
        )

    def test_jd_data_visible_in_orders_all(self, api_client, api_tokens):
        """After JD upload, /orders_all/ should return the JD rows."""
        csv = _make_jd_csv()
        upload_and_poll(api_client, _auth(api_tokens["admin"]), csv, "jd_orders.csv")
        r = api_client.get("/orders_all/", headers=_auth(api_tokens["admin"]))
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) >= 3


class TestUploadTmall:
    """Uploading Tmall data via /upload/ should succeed."""

    def test_upload_tmall_csv_returns_202(self, api_client, api_tokens):
        csv = _make_tmall_csv()
        data = upload_and_poll(api_client, _auth(api_tokens["admin"]), csv, "tmall_orders.csv")
        assert data["inserted_rows"] >= 1

    def test_upload_tmall_returns_platform_name(self, api_client, api_tokens):
        csv = _make_tmall_csv()
        data = upload_and_poll(api_client, _auth(api_tokens["admin"]), csv, "tmall_orders.csv")
        assert data.get("platform") == "tmall"


class TestUploadMultiplePlatforms:
    """Uploading data from multiple platforms should coexist in the same DB."""

    def test_youzan_and_jd_coexist(self, api_client, api_tokens):
        """Upload Youzan then JD — both should be present in orders_all."""
        yz_csv = _make_youzan_csv()
        jd_csv = _make_jd_csv()

        upload_and_poll(api_client, _auth(api_tokens["admin"]), yz_csv, "youzan.csv")
        upload_and_poll(api_client, _auth(api_tokens["admin"]), jd_csv, "jd.csv")

        r_all = api_client.get("/orders_all/", headers=_auth(api_tokens["admin"]))
        assert r_all.status_code == 200
        rows = r_all.json()
        # Should have rows from both platforms
        assert len(rows) >= 5, f"Expected ≥5 rows, got {len(rows)}"

    def test_all_three_platforms_coexist(self, api_client, api_tokens):
        """Upload Youzan, JD, and Tmall — all should coexist."""
        for name, csv_fn in [("yz.csv", _make_youzan_csv), ("jd.csv", _make_jd_csv), ("tm.csv", _make_tmall_csv)]:
            upload_and_poll(api_client, _auth(api_tokens["admin"]), csv_fn(), name)

        r_all = api_client.get("/orders_all/", headers=_auth(api_tokens["admin"]))
        assert r_all.status_code == 200
        rows = r_all.json()
        assert len(rows) >= 7


# ===========================================================================
# 8. Analysis API — Platform Filtering
# ===========================================================================

class TestAnalysisPlatformFilter:
    """Analysis endpoints should accept an optional `platform` query param."""

    def _upload_all(self, client, tokens):
        """Upload data from all three platforms."""
        for name, csv_fn in [("yz.csv", _make_youzan_csv), ("jd.csv", _make_jd_csv), ("tm.csv", _make_tmall_csv)]:
            upload_and_poll(client, _auth(tokens["admin"]), csv_fn(), name)

    def test_analysis_without_platform_returns_all(self, api_client, api_tokens):
        """GET /analysis/ without platform param should return data from all platforms."""
        self._upload_all(api_client, api_tokens)
        params = {"start_date": "2025-01-01", "end_date": "2027-01-01"}
        r = api_client.get("/analysis/", params=params, headers=_auth(api_tokens["analyst"]))
        assert r.status_code == 200
        data = r.json()
        total = data["old"]["count"] + data["new"]["count"]
        assert total >= 7

    def test_analysis_with_platform_jd(self, api_client, api_tokens):
        """GET /analysis/?platform=jd should return only JD data."""
        self._upload_all(api_client, api_tokens)
        params = {"start_date": "2025-01-01", "end_date": "2027-01-01", "platform": "jd"}
        r = api_client.get("/analysis/", params=params, headers=_auth(api_tokens["analyst"]))
        assert r.status_code == 200
        data = r.json()
        total = data["old"]["count"] + data["new"]["count"]
        assert total == 3, f"Expected 3 JD orders, got {total}"

    def test_analysis_with_platform_tmall(self, api_client, api_tokens):
        """GET /analysis/?platform=tmall should return only Tmall data."""
        self._upload_all(api_client, api_tokens)
        params = {"start_date": "2025-01-01", "end_date": "2027-01-01", "platform": "tmall"}
        r = api_client.get("/analysis/", params=params, headers=_auth(api_tokens["analyst"]))
        assert r.status_code == 200
        data = r.json()
        total = data["old"]["count"] + data["new"]["count"]
        assert total == 2, f"Expected 2 Tmall orders, got {total}"

    def test_analysis_with_platform_youzan(self, api_client, api_tokens):
        """GET /analysis/?platform=youzan should return only Youzan data."""
        self._upload_all(api_client, api_tokens)
        params = {"start_date": "2025-01-01", "end_date": "2027-01-01", "platform": "youzan"}
        r = api_client.get("/analysis/", params=params, headers=_auth(api_tokens["analyst"]))
        assert r.status_code == 200
        data = r.json()
        total = data["old"]["count"] + data["new"]["count"]
        assert total == 2, f"Expected 2 Youzan orders, got {total}"

    def test_overview_with_platform_filter(self, api_client, api_tokens):
        """GET /analysis/overview?platform=jd should scope to JD only."""
        self._upload_all(api_client, api_tokens)
        params = {"start_date": "2025-01-01", "end_date": "2027-01-01", "platform": "jd"}
        r = api_client.get("/analysis/overview", params=params, headers=_auth(api_tokens["analyst"]))
        assert r.status_code == 200
        data = r.json()
        assert data["orders"] == 3

    def test_repurchase_rate_with_platform_filter(self, api_client, api_tokens):
        """GET /analysis/repurchase_rate?platform=jd should scope to JD only."""
        self._upload_all(api_client, api_tokens)
        params = {"start_date": "2025-01-01", "end_date": "2027-01-01", "platform": "jd"}
        r = api_client.get("/analysis/repurchase_rate", params=params, headers=_auth(api_tokens["analyst"]))
        assert r.status_code == 200
        data = r.json()
        # JD fixture has 2 orders from same address → 1 repeat customer out of 2
        assert data["new_customers"] >= 1

    def test_customers_with_platform_filter(self, api_client, api_tokens):
        """GET /analysis/customers?platform=jd should return only JD customers."""
        self._upload_all(api_client, api_tokens)
        params = {"start_date": "2025-01-01", "end_date": "2027-01-01", "platform": "jd"}
        r = api_client.get("/analysis/customers", params=params, headers=_auth(api_tokens["analyst"]))
        assert r.status_code == 200
        data = r.json()
        # JD has 2 unique addresses (customers)
        assert len(data) == 2


# ===========================================================================
# 9. Customer Endpoint — Address-based Lookup
# ===========================================================================

class TestCustomerEndpointMultiPlatform:
    """The customer detail endpoint takes the exact customer_key as returned by
    GET /analysis/customers — never a partial/keyword search. A substring match
    here would let one customer's address-based key accidentally swallow another
    customer's orders when one address is a literal prefix of another (e.g. same
    building, different unit)."""

    def test_jd_customer_by_exact_address(self, api_client, api_tokens):
        """Looking up by the exact JD customer_key (full address) finds that customer."""
        jd_csv = _make_jd_csv()
        api_client.post(
            "/upload/",
            files={"file": ("jd.csv", jd_csv)},
            headers=_auth(api_tokens["admin"]),
        )

        params = {"start_date": "2025-01-01", "end_date": "2027-01-01"}
        r = api_client.get(
            "/analysis/customers/广东深圳市福田区示例街道1号",
            params=params,
            headers=_auth(api_tokens["analyst"]),
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data["count"] >= 1

        # A partial keyword (e.g. just the district) must NOT match — that
        # substring behavior previously let unrelated customers' orders bleed
        # into each other's detail view. No exact match → 404.
        r2 = api_client.get(
            "/analysis/customers/福田区",
            params=params,
            headers=_auth(api_tokens["analyst"]),
        )
        assert r2.status_code == 404

    def test_tmall_customer_by_exact_address(self, api_client, api_tokens):
        """Looking up by the exact Tmall customer_key (parsed address) finds that customer."""
        tm_csv = _make_tmall_csv()
        api_client.post(
            "/upload/",
            files={"file": ("tm.csv", tm_csv)},
            headers=_auth(api_tokens["admin"]),
        )

        params = {"start_date": "2025-01-01", "end_date": "2027-01-01"}
        r = api_client.get(
            "/analysis/customers/江苏省 扬州市 广陵区 示例地址",
            params=params,
            headers=_auth(api_tokens["analyst"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1

        r2 = api_client.get(
            "/analysis/customers/广陵区",
            params=params,
            headers=_auth(api_tokens["analyst"]),
        )
        assert r2.status_code == 404

    def test_youzan_customer_by_phone(self, api_client, api_tokens):
        """Youzan customers should still be searchable by phone number."""
        yz_csv = _make_youzan_csv()
        api_client.post(
            "/upload/",
            files={"file": ("yz.csv", yz_csv)},
            headers=_auth(api_tokens["admin"]),
        )

        params = {"start_date": "2025-01-01", "end_date": "2027-01-01"}
        r = api_client.get(
            "/analysis/customers/13900000001",
            params=params,
            headers=_auth(api_tokens["analyst"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1


# ===========================================================================
# 10. _normalize_youzan — Tab-stripping & address whitespace fix
# ===========================================================================

@pytest.fixture
def youzan_df_with_tabs():
    """Youzan rows that replicate the real CSV export artefact: phone numbers
    carry a leading tab character (``\\t13900000001``), and the address column
    has a trailing space — exactly as Youzan exports them."""
    return pd.DataFrame([
        {
            "订单号": "YZ-EX-0001",
            "买家付款时间": "2025-07-20 16:42:54",
            "订单实付金额": "539.10",
            "全部商品名称": "商品甲2盒尊享礼品装",
            "商品种类数": "1",
            "收货人/提货人": "王芳",
            # Leading tab — the real Youzan CSV bug
            "收货人手机号/提货人手机号": "\t13900000001",
            "收货人省份": "山东省",
            "收货人城市": "济南市",
            "收货人地区": "历城区",
            # Trailing space — also real
            "详细收货地址/提货地址": "山东省济南市历城区示例街道1号 ",
            "买家昵称": "昵称甲",
            "优惠券码名称": "9折优惠券",
            "分销员": "导购甲",
        },
        {
            "订单号": "YZ-EX-0002",
            "买家付款时间": "2025-07-20 16:00:42",
            "订单实付金额": "539.10",
            "全部商品名称": "商品甲2盒尊享礼品装",
            "商品种类数": "1",
            "收货人/提货人": "刘洋",
            "收货人手机号/提货人手机号": "\t13900000002",
            "收货人省份": "广东省",
            "收货人城市": "湛江市",
            "收货人地区": "徐闻县",
            "详细收货地址/提货地址": "广东省湛江市徐闻县示例街道1号（左边） ",
            "买家昵称": "昵称乙",
            "优惠券码名称": "9折优惠券",
            "分销员": "导购甲",
        },
    ], dtype=str)


class TestNormalizeYouzanTabStripping:
    """_normalize_youzan must strip the leading \\t that Youzan injects into
    numeric columns such as the phone number field."""

    def test_customer_key_has_no_leading_tab(self, youzan_df_with_tabs):
        """客户标识 must be a bare phone number — no leading tab."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df_with_tabs)
        for val in result["客户标识"]:
            assert not str(val).startswith("\t"), (
                f"customer_key still has leading tab: {repr(val)}"
            )

    def test_customer_key_equals_clean_phone(self, youzan_df_with_tabs):
        """客户标识 values must equal the plain digits, not the tab-prefixed form."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df_with_tabs)
        assert result["客户标识"].iloc[0] == "13900000001"
        assert result["客户标识"].iloc[1] == "13900000002"

    def test_phone_column_has_no_leading_tab(self, youzan_df_with_tabs):
        """收货人手机号/提货人手机号 (receiver_phone) must also be stripped."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df_with_tabs)
        for val in result["收货人手机号/提货人手机号"]:
            assert not str(val).startswith("\t"), (
                f"receiver_phone still has leading tab: {repr(val)}"
            )

    def test_phone_column_value_after_strip(self, youzan_df_with_tabs):
        """Phone column value must equal clean digits."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df_with_tabs)
        assert result["收货人手机号/提货人手机号"].iloc[0] == "13900000001"

    def test_address_trailing_space_stripped(self, youzan_df_with_tabs):
        """详细收货地址/提货地址 must have no leading or trailing whitespace."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df_with_tabs)
        for val in result["详细收货地址/提货地址"]:
            assert val == val.strip(), (
                f"address still has surrounding whitespace: {repr(val)}"
            )

    def test_address_content_preserved(self, youzan_df_with_tabs):
        """Stripping must not remove any meaningful address content."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df_with_tabs)
        assert "示例街道1号" in result["详细收货地址/提货地址"].iloc[0]
        assert "示例街道1号" in result["详细收货地址/提货地址"].iloc[1]

    def test_clean_input_unchanged(self, youzan_df):
        """If the input already has no tabs or extra whitespace, behaviour is
        identical to before the fix — no data is corrupted."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df)
        assert result["客户标识"].iloc[0] == "13900000001"
        assert result["客户标识"].iloc[1] == "13900000002"
        assert result["详细收货地址/提货地址"].iloc[0] == (
            "山东省济南市历城区示例街道1号"
        )

    def test_both_rows_are_preserved(self, youzan_df_with_tabs):
        """No rows should be silently dropped during tab-stripping."""
        from app.db.etl import normalize_dataframe
        result, _ = normalize_dataframe(youzan_df_with_tabs)
        assert len(result) == 2


class TestNormalizeYouzanIngestIntegration:
    """Verify that after tab-stripping, ingest() writes clean values to
    Customer and Order ORM objects."""

    def _make_session(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []
        session.flush = MagicMock()
        session.commit = MagicMock()
        return session

    def test_ingest_customer_key_no_tab(self, youzan_df_with_tabs):
        """Customer.customer_key must not contain a tab after ingest."""
        from app.db.etl import normalize_dataframe, ingest
        from app.db.models import Customer

        norm_df, _ = normalize_dataframe(youzan_df_with_tabs)
        session = self._make_session()
        added = []
        session.add.side_effect = added.append

        ingest(norm_df, session)

        customers = [o for o in added if isinstance(o, Customer)]
        assert customers, "Expected at least one Customer to be created"
        for c in customers:
            assert not c.customer_key.startswith("\t"), (
                f"Customer.customer_key has leading tab: {repr(c.customer_key)}"
            )

    def test_ingest_receiver_phone_no_tab(self, youzan_df_with_tabs):
        """Order.receiver_phone must not contain a tab after ingest."""
        from app.db.etl import normalize_dataframe, ingest
        from app.db.models import Order

        norm_df, _ = normalize_dataframe(youzan_df_with_tabs)
        session = self._make_session()
        added = []
        session.add.side_effect = added.append

        ingest(norm_df, session)

        orders = [o for o in added if isinstance(o, Order)]
        assert orders, "Expected at least one Order to be created"
        for o in orders:
            if o.receiver_phone:
                assert not o.receiver_phone.startswith("\t"), (
                    f"Order.receiver_phone has leading tab: {repr(o.receiver_phone)}"
                )

    def test_ingest_full_address_no_trailing_space(self, youzan_df_with_tabs):
        """Order.full_address must have no trailing whitespace."""
        from app.db.etl import normalize_dataframe, ingest
        from app.db.models import Order

        norm_df, _ = normalize_dataframe(youzan_df_with_tabs)
        session = self._make_session()
        added = []
        session.add.side_effect = added.append

        ingest(norm_df, session)

        orders = [o for o in added if isinstance(o, Order)]
        for o in orders:
            if o.full_address:
                assert o.full_address == o.full_address.strip(), (
                    f"Order.full_address has surrounding whitespace: {repr(o.full_address)}"
                )

    def test_ingest_full_address_content_correct(self, youzan_df_with_tabs):
        """Order.full_address must still contain the real address text."""
        from app.db.etl import normalize_dataframe, ingest
        from app.db.models import Order

        norm_df, _ = normalize_dataframe(youzan_df_with_tabs)
        session = self._make_session()
        added = []
        session.add.side_effect = added.append

        ingest(norm_df, session)

        orders = [o for o in added if isinstance(o, Order)]
        addresses = {o.full_address for o in orders if o.full_address}
        assert any("示例街道1号" in a for a in addresses), (
            f"Expected '示例街道1号' in some address, got: {addresses}"
        )

    def test_ingest_creates_two_distinct_customers(self, youzan_df_with_tabs):
        """Two rows with different (tab-prefixed) phones → two distinct
        Customer records with clean, distinct customer_keys."""
        from app.db.etl import normalize_dataframe, ingest
        from app.db.models import Customer

        norm_df, _ = normalize_dataframe(youzan_df_with_tabs)
        session = self._make_session()
        added = []
        session.add.side_effect = added.append

        ingest(norm_df, session)

        customers = [o for o in added if isinstance(o, Customer)]
        keys = {c.customer_key for c in customers}
        assert len(keys) == 2, (
            f"Expected 2 distinct customer_keys, got: {keys}"
        )


# ---------------------------------------------------------------------------
# Real-file smoke tests: assert no tab leaks through the full pipeline
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.path.exists(YOUZAN_FILE),
    reason="Youzan example CSV not found",
)
class TestRealYouzanFileTabStripping:
    """Integration smoke tests against the actual Youzan export in data/."""

    def test_no_tab_in_customer_key(self):
        """customer_key must be free of tab characters in the real file."""
        from app.db.etl import normalize_dataframe
        df = pd.read_csv(YOUZAN_FILE, dtype=str)
        result, platform = normalize_dataframe(df)
        assert platform == "youzan"
        tab_rows = result[result["客户标识"].str.startswith("\t")]
        assert len(tab_rows) == 0, (
            f"{len(tab_rows)} rows still have a leading tab in customer_key"
        )

    def test_no_tab_in_phone_column(self):
        """receiver_phone column must be free of tab characters in the real file."""
        from app.db.etl import normalize_dataframe
        df = pd.read_csv(YOUZAN_FILE, dtype=str)
        result, _ = normalize_dataframe(df)
        phone_col = "收货人手机号/提货人手机号"
        tab_rows = result[result[phone_col].str.startswith("\t")]
        assert len(tab_rows) == 0, (
            f"{len(tab_rows)} rows still have a leading tab in {phone_col}"
        )

    def test_no_trailing_space_in_address(self):
        """full_address must not have trailing spaces in the real file."""
        from app.db.etl import normalize_dataframe
        df = pd.read_csv(YOUZAN_FILE, dtype=str)
        result, _ = normalize_dataframe(df)
        addr_col = "详细收货地址/提货地址"
        dirty = result[
            result[addr_col].notna()
            & (result[addr_col] != result[addr_col].str.strip())
        ]
        assert len(dirty) == 0, (
            f"{len(dirty)} rows still have surrounding whitespace in address"
        )

    def test_customer_key_looks_like_phone(self):
        """For Youzan, customer_key (phone number) should be all digits,
        allowing a small tolerance for edge cases (e.g. Hong Kong numbers)."""
        from app.db.etl import normalize_dataframe
        df = pd.read_csv(YOUZAN_FILE, dtype=str)
        result, _ = normalize_dataframe(df)
        valid_keys = result["客户标识"].dropna()
        non_numeric = valid_keys[~valid_keys.str.match(r"^\d+$")]
        assert len(non_numeric) / len(valid_keys) < 0.05, (
            f"Too many non-numeric customer_keys: {non_numeric.tolist()[:5]}"
        )


# ===========================================================================
# 11. Real-file field-level coverage: jd-example.xlsx & tmall-example.xlsx
#
#  Goal: guarantee that every Order DB field that CAN be populated from each
#  platform's export IS populated correctly when a real file is ingested.
#
#  Tests are split into:
#    • TestRealJDFileFieldMapping   — jd-example.xlsx
#    • TestRealTmallFileFieldMapping — tmall-example.xlsx
#
#  Each test is skipped if the corresponding file is missing so the suite
#  stays green in CI environments that do not ship the example data.
# ===========================================================================

def _ingest_to_orders(df):
    """Normalise *df*, run ingest() against a mock session, and return the
    list of Order ORM objects that were added."""
    from app.db.etl import normalize_dataframe, ingest
    from app.db.models import Order

    norm_df, _ = normalize_dataframe(df)
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = []
    session.flush = MagicMock()
    session.commit = MagicMock()
    added = []
    session.add.side_effect = added.append

    ingest(norm_df, session)
    return [o for o in added if isinstance(o, Order)]


def _ingest_to_customers(df):
    """Same as _ingest_to_orders but returns Customer objects."""
    from app.db.etl import normalize_dataframe, ingest
    from app.db.models import Customer

    norm_df, _ = normalize_dataframe(df)
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = []
    session.flush = MagicMock()
    session.commit = MagicMock()
    added = []
    session.add.side_effect = added.append

    ingest(norm_df, session)
    return [o for o in added if isinstance(o, Customer)]


# ---------------------------------------------------------------------------
# JD — jd-example.xlsx
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(JD_FILE), reason="JD example file not found")
class TestRealJDFileFieldMapping:
    """Every DB field that JD provides must be non-null / correctly valued
    after the full normalize → ingest pipeline."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self._df = pd.read_excel(JD_FILE, dtype=str)
        self._orders = _ingest_to_orders(self._df)
        self._customers = _ingest_to_customers(self._df)

    # ── Row counts ────────────────────────────────────────────────────────

    def test_all_rows_ingested(self):
        """Each unique order_id in the JD file must produce exactly one Order record.

        JD exports can include multiple product lines for the same order (same
        订单号), which appear as separate rows.  Since deduplication is now
        based on order_id alone, only one Order is inserted per unique 订单号.
        """
        unique_order_ids = self._df["订单号"].dropna().nunique()
        assert len(self._orders) == unique_order_ids, (
            f"Expected {unique_order_ids} orders (unique 订单号), "
            f"got {len(self._orders)}"
        )

    def test_sku_merged_for_duplicate_order_id(self):
        """When an order has multiple rows, their SKUs should be joined and quantities summed."""
        # Find an order_id that appears multiple times in the source file
        counts = self._df["订单号"].value_counts()
        dup_id = counts[counts > 1].index[0]
        
        # Get the source rows for this order
        src_rows = self._df[self._df["订单号"] == dup_id]
        
        # Calculate expected values
        expected_sku = "、".join(src_rows["商品名称"].dropna().astype(str))
        expected_qty = sum(pd.to_numeric(src_rows["订购数量"], errors="coerce").fillna(0))
        
        # Find the ingested order
        ingested_order = next(o for o in self._orders if o.order_id == dup_id)
        
        # Assertions
        assert ingested_order.sku == expected_sku, (
            f"Merged SKU mismatch. Expected: {expected_sku!r}, Got: {ingested_order.sku!r}"
        )
        assert ingested_order.quantity == expected_qty, (
            f"Summed quantity mismatch. Expected: {expected_qty}, Got: {ingested_order.quantity}"
        )

    def test_distinct_customers_created(self):
        """Distinct delivery addresses should produce distinct Customer records."""
        unique_addrs = self._df["客户地址"].dropna().nunique()
        customer_keys = {c.customer_key for c in self._customers}
        assert len(customer_keys) == unique_addrs, (
            f"Expected {unique_addrs} customers, got {len(customer_keys)}"
        )

    # ── order_id ─────────────────────────────────────────────────────────

    def test_order_id_populated(self):
        """Order.order_id must be set for every row (mapped from '订单号')."""
        for o in self._orders:
            assert o.order_id is not None, "order_id should not be None"
            assert str(o.order_id).strip() != "", "order_id should not be empty"

    def test_order_id_matches_source(self):
        """The first order_id must match the first '订单号' in the source file."""
        src_id = str(self._df["订单号"].iloc[0]).strip()
        assert str(self._orders[0].order_id) == src_id, (
            f"order_id mismatch: {self._orders[0].order_id!r} vs {src_id!r}"
        )

    # ── order_date ───────────────────────────────────────────────────────

    def test_order_date_populated(self):
        """Order.order_date must be a date object for every row (from '下单时间')."""
        for o in self._orders:
            assert o.order_date is not None, "order_date should not be None"

    def test_order_date_type(self):
        """Order.order_date must be a datetime.date instance."""
        for o in self._orders:
            assert isinstance(o.order_date, date), (
                f"order_date is {type(o.order_date)}, expected date"
            )

    def test_order_date_reasonable_range(self):
        """All order dates must fall within a plausible range."""
        for o in self._orders:
            assert date(2020, 1, 1) <= o.order_date <= date(2030, 12, 31), (
                f"order_date out of range: {o.order_date}"
            )

    # ── platform ─────────────────────────────────────────────────────────

    def test_platform_is_jd(self):
        """Order.platform must be 'jd' for every row."""
        for o in self._orders:
            assert o.platform == "jd", f"Expected 'jd', got {o.platform!r}"

    # ── sku (商品名称) ───────────────────────────────────────────────────

    def test_sku_populated(self):
        """Order.sku must be set (mapped from '商品名称')."""
        for o in self._orders:
            assert o.sku is not None and o.sku.strip() != "", (
                "sku should not be empty"
            )

    def test_sku_matches_source(self):
        """First order's sku must contain text from the source '商品名称'."""
        src_sku = str(self._df["商品名称"].iloc[0]).strip()
        assert src_sku in (self._orders[0].sku or ""), (
            f"sku mismatch: {self._orders[0].sku!r} vs source {src_sku!r}"
        )

    # ── quantity (订购数量) ──────────────────────────────────────────────

    def test_quantity_populated(self):
        """Order.quantity must be a positive integer (mapped from '订购数量')."""
        for o in self._orders:
            assert o.quantity is not None, "quantity should not be None"
            assert o.quantity > 0, f"quantity should be positive, got {o.quantity}"

    # ── price (订单金额) ─────────────────────────────────────────────────

    def test_price_populated(self):
        """Order.price must be non-None (mapped from '订单金额').
        Some JD rows legitimately have price=0 (e.g. '(删除)暂停' status), so
        we only assert it is set and non-negative, not strictly positive."""
        for o in self._orders:
            assert o.price is not None, "price should not be None"
            assert float(o.price) >= 0, f"price should be non-negative, got {o.price}"

    def test_price_matches_source(self):
        """First order's price must match the source '订单金额'."""
        src_price = float(str(self._df["订单金额"].iloc[0]).strip())
        assert abs(float(self._orders[0].price) - src_price) < 0.01, (
            f"price mismatch: {self._orders[0].price} vs {src_price}"
        )

    # ── receiver (客户姓名) ──────────────────────────────────────────────

    def test_receiver_populated(self):
        """Order.receiver must be set (mapped from '客户姓名')."""
        for o in self._orders:
            assert o.receiver is not None and o.receiver.strip() != "", (
                "receiver should not be empty"
            )

    def test_receiver_matches_source(self):
        """First order's receiver must match source '客户姓名'."""
        src = str(self._df["客户姓名"].iloc[0]).strip()
        assert self._orders[0].receiver == src, (
            f"receiver mismatch: {self._orders[0].receiver!r} vs {src!r}"
        )

    # ── receiver_phone (联系电话) ────────────────────────────────────────

    def test_receiver_phone_populated(self):
        """Order.receiver_phone must be set (mapped from '联系电话').
        JD masks phone numbers, so we just check it is non-empty."""
        for o in self._orders:
            assert o.receiver_phone is not None and o.receiver_phone.strip() != "", (
                "receiver_phone should not be empty"
            )

    # ── full_address (客户地址) ──────────────────────────────────────────

    def test_full_address_populated(self):
        """Order.full_address must be set (mapped from '客户地址')."""
        for o in self._orders:
            assert o.full_address is not None and o.full_address.strip() != "", (
                "full_address should not be empty"
            )

    def test_full_address_matches_source(self):
        """First order's full_address must match source '客户地址'."""
        src = str(self._df["客户地址"].iloc[0]).strip()
        assert self._orders[0].full_address == src, (
            f"full_address mismatch: {self._orders[0].full_address!r} vs {src!r}"
        )

    def test_full_address_no_whitespace_padding(self):
        """full_address must have no leading or trailing whitespace."""
        for o in self._orders:
            if o.full_address:
                assert o.full_address == o.full_address.strip(), (
                    f"full_address has padding: {o.full_address!r}"
                )

    # ── customer_key (= full_address for JD) ────────────────────────────

    def test_customer_key_equals_address(self):
        """For JD, customer_key must equal the delivery address."""
        for o in self._orders:
            assert o.customer_key == o.full_address, (
                f"customer_key {o.customer_key!r} != full_address {o.full_address!r}"
            )

    def test_customer_key_no_whitespace_padding(self):
        """customer_key must have no surrounding whitespace."""
        for o in self._orders:
            assert o.customer_key == o.customer_key.strip(), (
                f"customer_key has padding: {o.customer_key!r}"
            )

    # ── Fields that JD does NOT provide (must be None, not crash) ────────

    def test_province_is_none_for_jd(self):
        """JD export has no province column — Order.province must be None."""
        for o in self._orders:
            assert o.province is None, (
                f"province should be None for JD, got {o.province!r}"
            )

    def test_area_is_none_for_jd(self):
        """JD export has no area column — Order.area must be None."""
        for o in self._orders:
            assert o.area is None, (
                f"area should be None for JD, got {o.area!r}"
            )

    def test_buyer_nick_is_none_for_jd(self):
        """JD export has no buyer_nick column — Order.buyer_nick must be None."""
        for o in self._orders:
            assert o.buyer_nick is None, (
                f"buyer_nick should be None for JD, got {o.buyer_nick!r}"
            )

    def test_coupon_name_is_none_for_jd(self):
        """JD export has no coupon column — Order.coupon_name must be None."""
        for o in self._orders:
            assert o.coupon_name is None, (
                f"coupon_name should be None for JD, got {o.coupon_name!r}"
            )

    def test_distributor_is_none_for_jd(self):
        """JD export has no distributor column — Order.distributor must be None."""
        for o in self._orders:
            assert o.distributor is None, (
                f"distributor should be None for JD, got {o.distributor!r}"
            )


# ---------------------------------------------------------------------------
# Tmall — tmall-example.xlsx
#
# NOTE: The example file ships with minimal data — only '订单编号', '总金额',
# and '收货地址' have real values; '订单创建时间' and '商品标题' are NaN.
# Tests document this known limitation explicitly rather than silently passing.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(TMALL_FILE), reason="Tmall example file not found")
class TestRealTmallFileFieldMapping:
    """Field-level tests for tmall-example.xlsx.

    The example file has sparse data (date and sku columns are empty), so
    ingest() skips all rows (order_date is required).  Tests verify that:
      1. The normalize pipeline maps every available column correctly.
      2. The *normalized* DataFrame has the right values before ingest.
      3. The known-empty columns are documented.
    """

    @pytest.fixture(autouse=True)
    def _load(self):
        self._df = pd.read_excel(TMALL_FILE, dtype=str)
        from app.db.etl import normalize_dataframe, _normalise
        self._norm, self._platform = normalize_dataframe(self._df)
        self._clean = _normalise(self._norm)

    # ── Platform detection ────────────────────────────────────────────────

    def test_platform_detected_as_tmall(self):
        assert self._platform == "tmall"

    # ── order_id mapping (订单编号 → 订单号) ────────────────────────────

    def test_order_id_column_present(self):
        """'订单号' must exist in the normalized DataFrame."""
        assert "订单号" in self._norm.columns

    def test_order_id_values_mapped(self):
        """'订单号' values must match source '订单编号'."""
        for i, row in self._df.iterrows():
            src = str(row["订单编号"]).strip()
            got = str(self._norm["订单号"].iloc[i]).strip()
            assert src == got, f"Row {i}: order_id {got!r} != source {src!r}"

    # ── price mapping (总金额 → 订单实付金额) ───────────────────────────

    def test_price_column_present(self):
        assert "订单实付金额" in self._norm.columns

    def test_price_values_mapped(self):
        """'订单实付金额' must contain the numeric total from '总金额'."""
        assert str(self._norm["订单实付金额"].iloc[0]).strip() == "520"
        assert str(self._norm["订单实付金额"].iloc[1]).strip() == "960"

    # ── address parsing (收货地址 → receiver / phone / full_address) ─────

    def test_receiver_name_parsed(self):
        """收货人/提货人 must be extracted from the composite '收货地址' field."""
        assert self._norm["收货人/提货人"].iloc[0] == "周强"
        assert self._norm["收货人/提货人"].iloc[1] == "孙强"

    def test_receiver_phone_parsed(self):
        """收货人手机号/提货人手机号 must be extracted and stripped of '86-' prefix.
        After _normalise(), an all-digit string may be coerced to int64 by
        pandas — compare as strings to stay robust."""
        assert str(self._norm["收货人手机号/提货人手机号"].iloc[0]).strip() == "13900000004"
        assert str(self._norm["收货人手机号/提货人手机号"].iloc[1]).strip() == "13900000003"

    def test_full_address_parsed(self):
        """详细收货地址/提货地址 must be the address portion only."""
        addr0 = self._norm["详细收货地址/提货地址"].iloc[0]
        assert "江苏省" in addr0, f"Expected province in address, got: {addr0!r}"
        assert "周强" not in addr0, "Name must not appear in parsed address"
        assert "13900000004" not in addr0, "Phone must not appear in parsed address"

    def test_full_address_no_trailing_separator(self):
        """Trailing '，' separators must be stripped from parsed address."""
        addr1 = self._norm["详细收货地址/提货地址"].iloc[1]
        assert not addr1.endswith("，"), (
            f"Address has trailing separator: {addr1!r}"
        )

    def test_full_address_no_whitespace_padding(self):
        """Parsed address must have no surrounding whitespace."""
        for val in self._norm["详细收货地址/提货地址"].dropna():
            assert val == val.strip(), f"Address has padding: {val!r}"

    # ── customer_key (= parsed address for Tmall) ────────────────────────

    def test_customer_key_equals_parsed_address(self):
        """For Tmall, customer_key must equal the parsed address portion."""
        for i in range(len(self._norm)):
            ck = self._norm["客户标识"].iloc[i]
            addr = self._norm["详细收货地址/提货地址"].iloc[i]
            assert ck == addr, (
                f"Row {i}: customer_key {ck!r} != full_address {addr!r}"
            )

    def test_two_customers_have_distinct_keys(self):
        """Two rows with different addresses must have different customer_keys."""
        k0 = self._norm["客户标识"].iloc[0]
        k1 = self._norm["客户标识"].iloc[1]
        assert k0 != k1, f"Both rows have the same customer_key: {k0!r}"

    # ── platform column ───────────────────────────────────────────────────

    def test_platform_column_set(self):
        """'平台' column must be 'tmall' for every row."""
        assert (self._norm["平台"] == "tmall").all()

    # ── known-sparse fields in the example file ───────────────────────────

    def test_order_date_is_null_in_example_file(self):
        """The example file's '订单创建时间' column is NaN — this is a known
        data-quality issue with the supplied sample, NOT a code bug.
        Document it explicitly so a future richer file will force this test
        to be updated."""
        raw_dates = self._df["订单创建时间"].dropna()
        assert len(raw_dates) == 0, (
            "Example file now has date values — update ingest integration tests"
        )

    def test_sku_is_null_in_example_file(self):
        """The example file's '商品标题' column is NaN — same caveat as above."""
        raw_skus = self._df["商品标题"].dropna()
        assert len(raw_skus) == 0, (
            "Example file now has SKU values — update ingest integration tests"
        )

    def test_ingest_skips_rows_when_date_missing(self):
        """Because order_date is required, ingest() must return 0 inserted rows
        when all dates are missing (as in the current example file)."""
        from app.db.etl import normalize_dataframe, ingest

        norm_df, _ = normalize_dataframe(self._df)
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []
        session.flush = MagicMock()
        session.commit = MagicMock()
        added = []
        session.add.side_effect = added.append

        count = ingest(norm_df, session)
        assert count == 0, (
            f"Expected 0 inserts (all dates missing), got {count}"
        )

    def test_ingest_with_complete_tmall_row(self):
        """A Tmall row that HAS a date and SKU must be fully ingested.
        Uses the inline tmall_df fixture (which has all required fields).
        This verifies the full ingest path, not just the normalize step."""
        from app.db.etl import normalize_dataframe, ingest
        from app.db.models import Order, Customer

        # Build a complete Tmall row (same schema as the real file)
        complete_df = pd.DataFrame([{
            "订单编号": "TM_COMPLETE_001",
            "总金额": "520",
            "收货地址": "周强，86-13900000004，江苏省 扬州市 广陵区 示例地址",
            "订单创建时间": "2026-02-01 09:00:00",
            "商品标题": "商品丁",
        }], dtype=str)

        norm_df, _ = normalize_dataframe(complete_df)
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []
        session.flush = MagicMock()
        session.commit = MagicMock()
        added = []
        session.add.side_effect = added.append

        count = ingest(norm_df, session)
        assert count == 1, f"Expected 1 insert, got {count}"

        orders = [o for o in added if isinstance(o, Order)]
        assert len(orders) == 1
        o = orders[0]
        assert o.order_id == "TM_COMPLETE_001"
        assert o.platform == "tmall"
        assert float(o.price) == 520.0
        assert o.receiver == "周强"
        assert o.receiver_phone == "13900000004"
        assert "江苏省" in (o.full_address or "")
        assert o.full_address == o.full_address.strip()

        customers = [o for o in added if isinstance(o, Customer)]
        assert len(customers) == 1
        assert customers[0].platform == "tmall"
        assert customers[0].customer_key == o.full_address

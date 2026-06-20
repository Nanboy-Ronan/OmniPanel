"""A small, fully synthetic order dataset for tests.

The test suite must not depend on real platform exports (those contain personal
data and are never committed). This module builds a deterministic in-memory
DataFrame in the 有赞/youzan export shape, so tests that need ingested data can
assert against known, fabricated values.

The dataset (all amounts in ¥):

  Customer (phone)  July orders            June orders
  13900000001       2 (100.00, 199.01)     -
  13900000002       2 (50.00, 50.00)       -
  13900000003       1 (300.00)             1 (200.00)
  13900000004       1 (80.00)              1 (150.00)
  13900000005       1 (120.00)             -
  13900000006       -                      1 (90.00)

  July  (2025-07-01..2025-07-31): 7 orders, 5 customers, revenue 899.01
  June  (2025-06-21..2025-06-30): 3 orders, 3 customers, revenue 440.00
  Repurchase customers in July (>=2 orders): 13900000001, 13900000002
  Top SKU by order count in July: 商品甲 (3 orders)
"""
from __future__ import annotations

from io import StringIO

import pandas as pd

# Header row mirrors a 有赞 order export; see app/db/etl/normalize.py for mapping.
_CSV = """\
订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额,收货人/提货人,收货人省份,收货人地区,详细收货地址/提货地址,买家昵称,优惠券码名称,分销员
S-J01,2025-07-05 09:00:00,13900000001,商品甲,1,100.00,张三,广东省,深圳市,示例路1号,buyer_a,满100减5,导购A
S-J02,2025-07-20 10:00:00,13900000001,商品甲,1,199.01,张三,广东省,深圳市,示例路1号,buyer_a,,导购A
S-J03,2025-07-06 11:00:00,13900000002,商品乙,1,50.00,李四,浙江省,杭州市,示例路2号,buyer_b,,导购B
S-J04,2025-07-22 12:00:00,13900000002,商品乙,1,50.00,李四,浙江省,杭州市,示例路2号,buyer_b,,导购B
S-J05,2025-07-10 13:00:00,13900000003,商品丙,1,300.00,王五,江苏省,南京市,示例路3号,buyer_c,,导购A
S-J06,2025-07-11 14:00:00,13900000004,商品甲,1,80.00,赵六,广东省,广州市,示例路4号,buyer_d,,导购B
S-J07,2025-07-12 15:00:00,13900000005,商品丁,1,120.00,孙七,北京市,朝阳区,示例路5号,buyer_e,,导购A
S-N01,2025-06-25 09:00:00,13900000003,商品丙,1,200.00,王五,江苏省,南京市,示例路3号,buyer_c,,导购A
S-N02,2025-06-26 10:00:00,13900000004,商品甲,1,150.00,赵六,广东省,广州市,示例路4号,buyer_d,,导购B
S-N03,2025-06-28 11:00:00,13900000006,商品乙,1,90.00,周八,四川省,成都市,示例路6号,buyer_f,,导购A
"""

# A known customer for single-customer lookups: 2 July orders, ¥299.01 total.
SAMPLE_CUSTOMER_PHONE = "13900000001"
SAMPLE_CUSTOMER_JULY_ORDER_COUNT = 2
SAMPLE_CUSTOMER_JULY_TOTAL = 299.01


def synthetic_youzan_df() -> pd.DataFrame:
    """Return the synthetic order dataset as a raw (un-normalized) DataFrame."""
    return pd.read_csv(StringIO(_CSV), dtype=str)

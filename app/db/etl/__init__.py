# rap/app/db/etl/__init__.py
"""Public ETL interface — re-exports the symbols imported by views and tests."""
from .detect import detect_platform
from .normalize import (
    normalize_dataframe,
    _normalise,
    _parse_tmall_address,
    _str_or_none,
    _int_or_none,
    _float_or_none,
    _date_or_none,
    _row_payload,
    _row_hash,
    _file_hash,
    _raw_order_id,
    _parse_normalized_rows,
    _invalid_reason,
    COL_ORDER_ID,
    COL_DATE,
    COL_CUSTOMER_KEY,
    COL_SKU,
    COL_QTY,
    COL_PRICE,
    COL_PLATFORM,
    COL_RECEIVER,
    COL_PHONE,
    COL_PROVINCE,
    COL_AREA,
    COL_ADDRESS,
    COL_NICK,
    COL_COUPON,
    COL_DISTRIB,
)
from .load import ingest, ingest_upload

__all__ = [
    "detect_platform",
    "normalize_dataframe",
    "ingest",
    "ingest_upload",
    "_normalise",
    "_parse_tmall_address",
]

# rap/app/db/etl/detect.py
"""Platform detection from DataFrame column fingerprints."""
import pandas as pd


def detect_platform(df: pd.DataFrame) -> str:
    """Identify the source platform from DataFrame column names.

    Returns ``"youzan"``, ``"jd"``, or ``"tmall"``.
    Raises ``ValueError`` for unrecognised schemas.
    """
    cols = set(df.columns)

    if "买家付款时间" in cols and "收货人手机号/提货人手机号" in cols:
        return "youzan"

    if "京东价" in cols and "客户地址" in cols:
        return "jd"

    if "订单编号" in cols and "收货地址" in cols:
        return "tmall"

    raise ValueError(
        f"Unrecognized platform — could not detect from columns: "
        f"{sorted(cols)[:10]}..."
    )

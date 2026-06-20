# rap/app/db/etl/normalize.py
"""DataFrame normalization: platform-specific transforms → unified schema."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional, Tuple

import pandas as pd

from ..raw_headers import YOUZAN_RAW_HEADERS, JD_RAW_HEADERS, TMALL_RAW_HEADERS
from .detect import detect_platform

# ── Unified column name constants ──────────────────────────────────────────────

COL_ORDER_ID    = "订单号"
COL_DATE        = "买家付款时间"
COL_CUSTOMER_KEY = "客户标识"
COL_SKU         = "全部商品名称"
COL_QTY         = "商品种类数"
COL_PRICE       = "订单实付金额"
COL_PLATFORM    = "平台"
COL_RECEIVER    = "收货人/提货人"
COL_PHONE       = "收货人手机号/提货人手机号"
COL_PROVINCE    = "收货人省份"
COL_AREA        = "收货人地区"
COL_ADDRESS     = "详细收货地址/提货地址"
COL_NICK        = "买家昵称"
COL_COUPON      = "优惠券码名称"
COL_DISTRIB     = "分销员"

# ── Platform → raw-headers map (populated after models are imported) ──────────
# Populated lazily to avoid a circular import at module load time.
_RAW_HEADERS_BY_PLATFORM = {
    "youzan": YOUZAN_RAW_HEADERS,
    "jd":     JD_RAW_HEADERS,
    "tmall":  TMALL_RAW_HEADERS,
}


def get_raw_headers(platform: str) -> list[str]:
    return _RAW_HEADERS_BY_PLATFORM[platform]


def get_raw_model(platform: str):
    from app.db.models import YouzanOrder, JdOrder, TmallOrder
    return {"youzan": YouzanOrder, "jd": JdOrder, "tmall": TmallOrder}[platform]


# ── Tmall address parser ───────────────────────────────────────────────────────

def _parse_tmall_address(raw: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse Tmall's composite address field.

    Format: ``"姓名，86-手机号，省 市 区 详细地址"``
    Returns ``(name, phone, address)``.  All values stripped.
    Returns ``(None, None, None)`` for empty or unparseable input.
    """
    if not raw or not raw.strip():
        return None, None, None

    raw = raw.strip()
    parts = re.split(r"[，,]", raw)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) < 3:
        return None, None, None

    name = parts[0].strip()
    phone_raw = parts[1].strip()
    phone = re.sub(r"^86-?", "", phone_raw).strip()
    addr = "，".join(parts[2:]).strip().rstrip("，").rstrip(",").strip()

    return name, phone, addr


# ── Platform-specific normalizers ─────────────────────────────────────────────

def _str_or_none(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s else None


def _normalize_youzan(df: pd.DataFrame) -> pd.DataFrame:
    phone_col = "收货人手机号/提货人手机号"
    if phone_col in df.columns:
        df[phone_col] = df[phone_col].apply(lambda v: _str_or_none(v) or "")

    addr_col = "详细收货地址/提货地址"
    if addr_col in df.columns:
        df[addr_col] = df[addr_col].apply(lambda v: _str_or_none(v) or "")

    df["客户标识"] = df[phone_col]
    return df


def _normalize_jd(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "下单时间":   "买家付款时间",
        "订单金额":   "订单实付金额",
        "商品名称":   "全部商品名称",
        "订购数量":   "商品种类数",
        "客户姓名":   "收货人/提货人",
        "联系电话":   "收货人手机号/提货人手机号",
        "客户地址":   "详细收货地址/提货地址",
    }
    for src, dst in rename_map.items():
        if src not in df.columns:
            continue
        if dst not in df.columns:
            df[dst] = df[src]
        else:
            blank = df[dst].isna() | (df[dst].astype(str).str.strip() == "")
            df.loc[blank, dst] = df.loc[blank, src]

    if "订单号" in df.columns:
        agg_cols = {}
        if "全部商品名称" in df.columns:
            agg_cols["全部商品名称"] = lambda x: "、".join(x.dropna().astype(str))
        if "商品种类数" in df.columns:
            df["商品种类数"] = pd.to_numeric(df["商品种类数"], errors="coerce").fillna(0)
            agg_cols["商品种类数"] = "sum"

        other_cols = {c: "first" for c in df.columns if c not in agg_cols and c != "订单号"}
        if agg_cols:
            df = df.groupby("订单号", as_index=False, sort=False).agg({**agg_cols, **other_cols})

    df["客户标识"] = df["详细收货地址/提货地址"]
    return df


def _normalize_tmall(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "订单编号":   "订单号",
        "总金额":     "订单实付金额",
        "商品标题":   "全部商品名称",
        "订单创建时间": "买家付款时间",
    }
    for src, dst in rename_map.items():
        if src not in df.columns:
            continue
        if dst not in df.columns:
            df[dst] = df[src]
        else:
            blank = df[dst].isna() | (df[dst].astype(str).str.strip() == "")
            df.loc[blank, dst] = df.loc[blank, src]

    if "商品种类数" not in df.columns:
        df["商品种类数"] = None

    parsed = df["收货地址"].apply(
        lambda x: _parse_tmall_address(str(x) if pd.notna(x) else "")
    )
    df["收货人/提货人"] = parsed.apply(lambda t: t[0])
    df["收货人手机号/提货人手机号"] = parsed.apply(lambda t: t[1])
    df["详细收货地址/提货地址"] = parsed.apply(lambda t: t[2])
    df["客户标识"] = df["详细收货地址/提货地址"]

    return df


# ── Generic identifier/type normalisation ────────────────────────────────────

def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    identifier_cols = {
        COL_ORDER_ID,
        COL_CUSTOMER_KEY,
        COL_PHONE,
        "订单编号",
        "联系电话",
    }
    for col in df.columns:
        if df[col].dtype == object:
            non_null = df[col].dropna()
            if non_null.empty:
                continue
            if col in identifier_cols:
                df[col] = df[col].apply(_str_or_none)
            elif non_null.astype(str).str.match(r"\d{4}-\d{2}-\d{2}").all():
                df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%F")
            elif non_null.astype(str).str.match(r"^-?\d+(\.\d+)?$").all():
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = df[col].replace({"": None})
    df = df.where(pd.notnull(df), None)
    return df


# ── Main entry point ───────────────────────────────────────────────────────────

def normalize_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Normalize a platform-specific DataFrame to a unified schema.

    Returns ``(normalized_df, platform_name)``.
    """
    platform = detect_platform(df)
    out = df.copy()

    if platform == "youzan":
        out = _normalize_youzan(out)
    elif platform == "jd":
        out = _normalize_jd(out)
    elif platform == "tmall":
        out = _normalize_tmall(out)

    out["平台"] = platform
    return out, platform


# ── Row helpers ────────────────────────────────────────────────────────────────

def _int_or_none(val) -> Optional[int]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _float_or_none(val) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _date_or_none(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, pd.Timestamp):
        return val.date()
    try:
        return pd.to_datetime(val, errors="coerce").date()
    except Exception:
        return None


def _jsonable_value(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    return str(val)


def _row_payload(row: pd.Series) -> dict:
    return {str(k): _jsonable_value(v) for k, v in row.items()}


def _row_hash(platform: str, payload: dict) -> str:
    raw = json.dumps(
        {"platform": platform, "row": payload},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_hash(df: pd.DataFrame) -> str:
    payload = [_row_payload(row) for _, row in df.iterrows()]
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _raw_order_id(platform: str, row: pd.Series) -> Optional[str]:
    if platform == "tmall":
        return _str_or_none(row.get("订单编号"))
    return _str_or_none(row.get("订单号"))


def _parse_normalized_rows(df: pd.DataFrame) -> list[dict]:
    parsed: list[dict] = []
    for _, row in df.iterrows():
        order_id     = _str_or_none(row.get(COL_ORDER_ID))
        order_date   = _date_or_none(row.get(COL_DATE))
        customer_key = _str_or_none(row.get(COL_CUSTOMER_KEY))
        parsed.append(dict(
            order_id=order_id,
            order_date=order_date,
            customer_key=customer_key,
            sku=_str_or_none(row.get(COL_SKU)),
            quantity=_int_or_none(row.get(COL_QTY)),
            price=_float_or_none(row.get(COL_PRICE)),
            platform=_str_or_none(row.get(COL_PLATFORM)) or "youzan",
            receiver=_str_or_none(row.get(COL_RECEIVER)),
            receiver_phone=_str_or_none(row.get(COL_PHONE)),
            province=_str_or_none(row.get(COL_PROVINCE)),
            area=_str_or_none(row.get(COL_AREA)),
            full_address=_str_or_none(row.get(COL_ADDRESS)),
            buyer_nick=_str_or_none(row.get(COL_NICK)),
            coupon_name=_str_or_none(row.get(COL_COUPON)),
            distributor=_str_or_none(row.get(COL_DISTRIB)),
        ))
    return parsed


def _invalid_reason(parsed_row: dict) -> Optional[str]:
    missing = []
    if parsed_row.get("customer_key") is None:
        missing.append("customer_key")
    if parsed_row.get("order_date") is None:
        missing.append("order_date")
    if missing:
        return "Missing or invalid required field(s): " + ", ".join(missing)
    price = parsed_row.get("price")
    if price is not None and float(price) < 0:
        return f"Negative price ({price})"
    return None

"""Order status classification and the revenue rule (see docs/order-status-proposal.md).

Every revenue, order-count and customer metric counts an order only when its
``status_group`` is in ``COUNTED_GROUPS``, and takes its amount from
``net_amount()`` (paid total minus refunds). Change what counts here, nowhere else.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import func

COMPLETED = "completed"  # paid and not cancelled: awaiting shipment, shipped or done
CLOSED = "closed"        # cancelled, closed or locked
UNPAID = "unpaid"        # created but never paid
DELETED = "deleted"      # JD "(删除)…": deleted orders; not counted (decided 2026-10-10)
UNKNOWN = "unknown"      # blank or unrecognised status, or ingested before statuses were kept

STATUS_GROUPS = (COMPLETED, CLOSED, UNPAID, DELETED, UNKNOWN)
COUNTED_GROUPS = (COMPLETED, UNKNOWN)

STATUS_LABELS = {
    COMPLETED: "已成交",
    CLOSED: "已关闭",
    UNPAID: "未付款",
    DELETED: "买家已删除",
    UNKNOWN: "状态未知",
}

DELETED_PREFIX = "(删除)"

# Manual exports and the JD collector use different words for the same states.
_MAPPING: dict[str, dict[str, str]] = {
    "youzan": {
        "待发货": COMPLETED, "已发货": COMPLETED, "交易完成": COMPLETED, "交易成功": COMPLETED,
        "部分发货": COMPLETED, "待成团": COMPLETED, "待接单": COMPLETED,
        "交易关闭": CLOSED, "已关闭": CLOSED,
        "待付款": UNPAID, "等待买家付款": UNPAID,
    },
    "jd": {
        "等待出库": COMPLETED, "待出库": COMPLETED, "等待确认收货": COMPLETED, "已发货": COMPLETED,
        "完成": COMPLETED, "已完成": COMPLETED, "等待境外出库": COMPLETED, "等待境内发货": COMPLETED,
        "已取消": CLOSED, "取消": CLOSED, "锁定": CLOSED, "锁定订单": CLOSED,
        "待付款": UNPAID, "等待付款": UNPAID,
        # 暂停/暂停订单 stay unknown: usually a pending cancellation, not yet decided.
    },
    "tmall": {
        "交易成功": COMPLETED, "卖家已发货": COMPLETED, "买家已付款": COMPLETED,
        "等待卖家发货": COMPLETED, "等待买家确认收货": COMPLETED,
        "交易关闭": CLOSED, "交易已关闭": CLOSED,
        "等待买家付款": UNPAID,
    },
}


def classify(platform: Optional[str], raw_status: Optional[str]) -> str:
    """Map a platform's raw 订单状态 to one of ``STATUS_GROUPS``."""
    status = (raw_status or "").strip()
    if not status:
        return UNKNOWN
    if platform == "jd" and status.startswith(DELETED_PREFIX):
        return DELETED
    return _MAPPING.get(platform or "", {}).get(status, UNKNOWN)


def parse_refund(value: object) -> Optional[Decimal]:
    """订单已退款金额 as a non-negative Decimal; blank, zero or junk becomes None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("¥", "")
    if not text or text.lower() == "nan":
        return None
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def counted(model=None):
    """WHERE clause selecting orders that count toward revenue and order counts."""
    if model is None:
        from app.db.models import Order as model  # noqa: N813
    return model.status_group.in_(COUNTED_GROUPS)


def net_amount(model=None):
    """Paid total minus refunds; NULL when the price itself is unknown."""
    if model is None:
        from app.db.models import Order as model  # noqa: N813
    return model.price - func.coalesce(model.refunded_amount, 0)


def counted_sql(alias: str = "") -> str:
    """``counted()`` as raw SQL, for the NL-SQL schema doc and text() queries."""
    prefix = f"{alias}." if alias else ""
    groups = ", ".join(f"'{g}'" for g in COUNTED_GROUPS)
    return f"{prefix}status_group IN ({groups})"

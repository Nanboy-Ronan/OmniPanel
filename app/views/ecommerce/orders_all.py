# rap/app/views/ecommerce/orders_all.py
import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime
from decimal import Decimal

from ...auth import current_active_user
from ...config import settings
from ...db import get_session
from ... import db as _db_mod
from ...db.models import JdOrder, Order, TmallOrder, YouzanOrder
from ...utils.logger import log_operation

router = APIRouter(prefix="/orders_all", tags=["orders"])

RAW_MODEL_BY_PLATFORM = {
    "youzan": YouzanOrder,
    "jd": JdOrder,
    "tmall": TmallOrder,
}


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _raw_row_to_dict(row) -> dict:
    return {
        prop.columns[0].name: _jsonable(getattr(row, prop.key))
        for prop in row.__mapper__.column_attrs
    }


_SEARCH_COLUMNS = (
    Order.order_id, Order.customer_key, Order.platform, Order.sku,
    Order.receiver, Order.receiver_phone, Order.province, Order.area,
    Order.full_address, Order.buyer_nick, Order.coupon_name, Order.distributor,
)
_EXPORT_COLUMNS = (
    "id", "order_id", "order_date", "customer_key", "platform", "sku",
    "quantity", "price", "receiver", "receiver_phone", "province", "area",
    "full_address", "buyer_nick", "coupon_name", "distributor",
)
_FILTER_COLUMNS = {name: getattr(Order, name) for name in _EXPORT_COLUMNS if name != "id"}
_NUMERIC_COLUMNS = {"quantity", "price"}
_DATE_COLUMNS = {"order_date"}


def _column_filter_conditions(raw_filters: str | None) -> list:
    if not raw_filters:
        return []
    try:
        filters = json.loads(raw_filters)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid column filters") from exc
    if not isinstance(filters, list) or len(filters) > 12:
        raise HTTPException(status_code=422, detail="Invalid column filters")
    conditions = []
    for item in filters:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="Invalid column filters")
        field = item.get("field")
        value = item.get("value")
        if not isinstance(field, str) or field not in _FILTER_COLUMNS:
            raise HTTPException(status_code=422, detail="Invalid column filters")
        column = _FILTER_COLUMNS[field]
        if field in _NUMERIC_COLUMNS | _DATE_COLUMNS:
            bounds = (("value", "eq"), ("min", "ge"), ("max", "le"))
            supplied = [(name, op) for name, op in bounds if name in item]
            if not supplied or ("value" in item and len(supplied) > 1):
                raise HTTPException(status_code=422, detail="Invalid column filters")
            parsed = {}
            for name, op in supplied:
                raw = item[name]
                if not isinstance(raw, str) or not raw or len(raw) > 200:
                    raise HTTPException(status_code=422, detail="Invalid column filters")
                try:
                    if field in _NUMERIC_COLUMNS:
                        parsed[name] = Decimal(raw)
                        if not parsed[name].is_finite():
                            raise ValueError
                    else:
                        parsed[name] = date.fromisoformat(raw)
                except (ValueError, ArithmeticError) as exc:
                    raise HTTPException(status_code=422, detail="Invalid range filter") from exc
                conditions.append({"eq": column.__eq__, "ge": column.__ge__, "le": column.__le__}[op](parsed[name]))
            if "min" in parsed and "max" in parsed and parsed["min"] > parsed["max"]:
                raise HTTPException(status_code=422, detail="Invalid range filter")
        else:
            if not isinstance(value, str) or not value or len(value) > 200:
                raise HTTPException(status_code=422, detail="Invalid column filters")
            literal = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(column.ilike(f"%{literal}%", escape="\\"))
    return conditions


def _order_filter(search: str | None, platform: str | None, column_filters: str | None = None):
    conditions = []
    if search:
        # Escape SQL wildcards so user input is treated as literal text.
        literal = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(or_(*(col.ilike(f"%{literal}%", escape="\\") for col in _SEARCH_COLUMNS)))
    if platform:
        conditions.append(Order.platform == platform)
    conditions.extend(_column_filter_conditions(column_filters))
    return conditions


def _order_to_dict(o: Order) -> dict:
    data = {name: _jsonable(getattr(o, name)) for name in _EXPORT_COLUMNS}
    data["price"] = float(o.price or 0)
    return data


@router.get("/", summary="Return a page of order rows as JSON")
async def get_all_orders(
    response: Response,
    limit: int = Query(
        None,
        ge=1,
        le=20000,
        description="Max rows to return. Defaults to settings.analysis_rows_cap.",
    ),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=200),
    platform: str | None = Query(None, pattern="^(youzan|jd|tmall)$"),
    column_filters: str | None = Query(None, max_length=2400),
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Paginated order listing — total row count is returned in the
    ``X-Total-Count`` response header so the client can show "N / total" and
    decide whether to fetch more, without the API ever serialising the whole
    table in one response.
    """
    effective_limit = limit or settings.analysis_rows_cap
    try:
        conditions = _order_filter(search, platform, column_filters)
        total = (await session.execute(select(func.count(Order.id)).where(*conditions))).scalar() or 0
        result = await session.execute(
            select(Order)
            .where(*conditions)
            .order_by(Order.order_date.desc(), Order.id.desc())
            .offset(offset)
            .limit(effective_limit)
        )
        orders = result.scalars().all()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    response.headers["X-Total-Count"] = str(total)

    rows = [_order_to_dict(o) for o in orders]

    await log_operation(str(_u.id), "download", {"count": len(rows)}, session=session)
    return rows


@router.get("/export", summary="Stream all matching order rows as CSV")
async def export_orders(
    search: str | None = Query(None, max_length=200),
    platform: str | None = Query(None, pattern="^(youzan|jd|tmall)$"),
    column_filters: str | None = Query(None, max_length=2400),
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    conditions = _order_filter(search, platform, column_filters)
    await log_operation(str(_u.id), "download", {"scope": "orders_export", "search": bool(search)}, session=session)

    async def rows():
        yield "\ufeff"
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_EXPORT_COLUMNS)
        yield buffer.getvalue()
        async with _db_mod.AsyncSessionLocal() as export_session:
            result = await export_session.stream(
                select(Order).where(*conditions).order_by(Order.order_date.desc(), Order.id.desc())
            )
            try:
                async for order in result.scalars():
                    buffer.seek(0)
                    buffer.truncate(0)
                    writer.writerow([_jsonable(getattr(order, name)) for name in _EXPORT_COLUMNS])
                    yield buffer.getvalue()
            finally:
                await result.close()

    return StreamingResponse(rows(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="filtered_orders.csv"',
        "Cache-Control": "no-store",
    })


@router.get("/{order_pk}/raw", summary="Return raw platform row(s) for one order")
async def get_order_raw_rows(
    order_pk: int,
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        order_result = await session.execute(select(Order).where(Order.id == order_pk))
        order = order_result.scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        model = RAW_MODEL_BY_PLATFORM.get(order.platform)
        if model is None:
            raise HTTPException(status_code=404, detail="Raw table not found for platform")

        raw_result = await session.execute(
            select(model)
            .where(model.normalized_order_id == order.order_id)
            .order_by(model.source_row_number)
        )
        raw_rows = raw_result.scalars().all()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "order": {
            "id": order.id,
            "order_id": order.order_id,
            "platform": order.platform,
        },
        "rows": [_raw_row_to_dict(row) for row in raw_rows],
        "row_count": len(raw_rows),
    }

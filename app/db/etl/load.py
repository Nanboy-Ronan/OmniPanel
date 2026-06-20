# rap/app/db/etl/load.py
"""Database ingestion: turn a normalized DataFrame into persisted rows."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import Customer, Order, UploadBatch, UploadRejectedRow
from .detect import detect_platform
from .normalize import (
    _file_hash,
    _invalid_reason,
    _jsonable_value,
    _normalise,
    _parse_normalized_rows,
    _raw_order_id,
    _row_hash,
    _row_payload,
    get_raw_headers,
    get_raw_model,
    normalize_dataframe,
)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _ensure_customers_for_orders(rows: list[dict], session: Session) -> None:
    if not rows:
        return
    incoming_keys = {r["customer_key"] for r in rows if r.get("customer_key")}
    if not incoming_keys:
        return
    existing_customers: dict[str, Customer] = {
        c.customer_key: c
        for c in session.execute(
            select(Customer).where(Customer.customer_key.in_(incoming_keys))
        ).scalars().all()
    }
    for r in rows:
        ck = r["customer_key"]
        if ck in existing_customers:
            c = existing_customers[ck]
            if c.first_order_date is None or r["order_date"] < c.first_order_date:
                c.first_order_date = r["order_date"]
        else:
            new_c = Customer(
                customer_key=ck,
                platform=r["platform"],
                first_order_date=r["order_date"],
            )
            session.add(new_c)
            existing_customers[ck] = new_c
    session.flush()


def _new_order_from_parsed(r: dict) -> Order:
    return Order(
        order_id=r["order_id"],
        order_date=r["order_date"],
        customer_key=r["customer_key"],
        platform=r["platform"],
        sku=r["sku"],
        quantity=r["quantity"],
        price=r["price"],
        receiver=r["receiver"],
        receiver_phone=r["receiver_phone"],
        province=r["province"],
        area=r["area"],
        full_address=r["full_address"],
        buyer_nick=r["buyer_nick"],
        coupon_name=r["coupon_name"],
        distributor=r["distributor"],
    )


def _add_rejected_row(
    session: Session,
    batch_id: int,
    platform: str,
    source_row_number: int,
    payload: dict,
    reason: str,
) -> None:
    session.add(UploadRejectedRow(
        batch_id=batch_id,
        platform=platform,
        source_row_number=source_row_number,
        raw_payload=payload,
        reason=reason,
    ))


def _add_platform_row(
    session: Session,
    platform: str,
    raw_row: pd.Series,
    source_row_number: int,
    batch_id: int,
    normalized_order_id: str,
    order_id: Optional[str],
    status: str = "inserted",
    message: Optional[str] = None,
) -> None:
    model = get_raw_model(platform)
    headers = get_raw_headers(platform)
    payload = _row_payload(raw_row)
    values = {
        "batch_id": batch_id,
        "source_row_number": source_row_number,
        "order_id": order_id,
        "normalized_order_id": normalized_order_id,
        "ingest_status": status,
        "ingest_message": message,
        "row_hash": _row_hash(platform, payload),
    }
    for idx, header in enumerate(headers):
        values[f"raw_col_{idx}"] = _jsonable_value(raw_row.get(header))
    session.add(model(**values))


def _existing_order_ids(session: Session, order_ids: set[str]) -> set[str]:
    if not order_ids:
        return set()
    return set(
        session.execute(
            select(Order.order_id).where(Order.order_id.in_(order_ids))
        ).scalars().all()
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def ingest_upload(
    df: pd.DataFrame,
    session: Session,
    *,
    filename: str,
    uploaded_by: str | None = None,
    file_sha256: str | None = None,
    batch_id: int | None = None,
) -> dict:
    """Ingest an uploaded file while preserving platform-specific raw rows."""
    raw_df = df.copy()
    platform = detect_platform(raw_df)
    norm_df, _ = normalize_dataframe(raw_df)
    norm_df = _normalise(norm_df)
    parsed = _parse_normalized_rows(norm_df)

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(77881101)"))

    if batch_id is not None:
        batch = session.get(UploadBatch, batch_id)
        if batch is None:
            raise ValueError(f"UploadBatch {batch_id} not found")
        batch.platform = platform
        batch.file_sha256 = file_sha256 or _file_hash(raw_df)
        batch.row_count = len(raw_df)
        session.flush()
    else:
        batch = UploadBatch(
            filename=filename,
            platform=platform,
            uploaded_by=uploaded_by,
            file_sha256=file_sha256 or _file_hash(raw_df),
            row_count=len(raw_df),
            status="completed",
        )
        session.add(batch)
        session.flush()

    inserted_orders = 0
    raw_rows_inserted = 0
    duplicate_rows = 0
    invalid_rows = 0

    if platform == "jd":
        by_order_id: dict[str | None, dict] = {}
        for parsed_row in parsed:
            by_order_id[parsed_row["order_id"]] = parsed_row

        raw_groups: dict[str | None, list[tuple[int, pd.Series]]] = {}
        for idx, raw_row in raw_df.iterrows():
            raw_groups.setdefault(_raw_order_id(platform, raw_row), []).append((idx + 1, raw_row))

        incoming_order_ids = {oid for oid in raw_groups if oid}
        existing_ids = _existing_order_ids(session, incoming_order_ids)
        in_batch_order_ids: set[str] = set()

        valid_to_insert: list[dict] = []
        for oid, rows in raw_groups.items():
            parsed_row = by_order_id.get(oid)
            if parsed_row is None:
                for source_row_number, raw_row in rows:
                    invalid_rows += 1
                    _add_rejected_row(
                        session, batch.id, platform, source_row_number,
                        _row_payload(raw_row), "Could not normalize JD order group",
                    )
                continue
            reason = _invalid_reason(parsed_row)
            if reason:
                for source_row_number, raw_row in rows:
                    invalid_rows += 1
                    _add_rejected_row(
                        session, batch.id, platform, source_row_number,
                        _row_payload(raw_row), reason,
                    )
                continue
            if oid and (oid in existing_ids or oid in in_batch_order_ids):
                duplicate_rows += len(rows)
                continue
            valid_to_insert.append(parsed_row)
            if oid:
                in_batch_order_ids.add(oid)

        _ensure_customers_for_orders(valid_to_insert, session)
        for parsed_row in valid_to_insert:
            order = _new_order_from_parsed(parsed_row)
            session.add(order)
            session.flush()
            inserted_orders += 1
            for source_row_number, raw_row in raw_groups.get(parsed_row["order_id"], []):
                _add_platform_row(
                    session, platform, raw_row, source_row_number,
                    batch.id, parsed_row["order_id"], parsed_row["order_id"],
                )
                raw_rows_inserted += 1
    else:
        incoming_order_ids = {r["order_id"] for r in parsed if r["order_id"]}
        existing_ids = _existing_order_ids(session, incoming_order_ids)
        in_batch_order_ids = set()

        valid_items: list[tuple[int, pd.Series, dict]] = []
        for idx, (raw_row, parsed_row) in enumerate(
            zip(raw_df.to_dict("records"), parsed), start=1
        ):
            raw_series = pd.Series(raw_row)
            reason = _invalid_reason(parsed_row)
            if reason:
                invalid_rows += 1
                _add_rejected_row(session, batch.id, platform, idx, _row_payload(raw_series), reason)
                continue
            oid = parsed_row["order_id"]
            if oid and (oid in existing_ids or oid in in_batch_order_ids):
                duplicate_rows += 1
                continue
            valid_items.append((idx, raw_series, parsed_row))
            if oid:
                in_batch_order_ids.add(oid)

        _ensure_customers_for_orders([item[2] for item in valid_items], session)
        for source_row_number, raw_row, parsed_row in valid_items:
            order = _new_order_from_parsed(parsed_row)
            session.add(order)
            session.flush()
            inserted_orders += 1
            _add_platform_row(
                session, platform, raw_row, source_row_number,
                batch.id, parsed_row["order_id"], parsed_row["order_id"],
            )
            raw_rows_inserted += 1

    batch.inserted_orders = inserted_orders
    batch.raw_rows_inserted = raw_rows_inserted
    batch.duplicate_rows = duplicate_rows
    batch.invalid_rows = invalid_rows
    if batch_id is not None:
        batch.status = "completed"
    session.commit()

    return {
        "batch_id": batch.id,
        "platform": platform,
        "total_rows": len(raw_df),
        "inserted_orders": inserted_orders,
        "inserted_rows": inserted_orders,
        "raw_rows_inserted": raw_rows_inserted,
        "duplicate_rows": duplicate_rows,
        "invalid_rows": invalid_rows,
    }


def ingest(df: pd.DataFrame, session: Session) -> int:
    """Ingest a normalized DataFrame of order rows into the database.

    Uses bulk pre-fetching to avoid N+1 queries.  Only one ``commit()`` is
    issued at the end.  Skips rows with missing customer_key or order_date.
    """
    from .normalize import (  # local import avoids circular at module load
        COL_ORDER_ID, COL_DATE, COL_CUSTOMER_KEY, COL_SKU, COL_QTY,
        COL_PRICE, COL_PLATFORM, COL_RECEIVER, COL_PHONE,
        COL_PROVINCE, COL_AREA, COL_ADDRESS, COL_NICK, COL_COUPON, COL_DISTRIB,
        _str_or_none, _int_or_none, _float_or_none, _date_or_none,
    )

    df = df.copy()
    if COL_CUSTOMER_KEY not in df.columns:
        df, _platform = normalize_dataframe(df)
    df = _normalise(df)

    parsed: list[dict] = []
    for _, row in df.iterrows():
        order_id     = _str_or_none(row.get(COL_ORDER_ID))
        order_date   = _date_or_none(row.get(COL_DATE))
        customer_key = _str_or_none(row.get(COL_CUSTOMER_KEY))
        if customer_key is None or order_date is None:
            continue
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

    if not parsed:
        return 0

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(77881101)"))

    incoming_keys = {r["customer_key"] for r in parsed}
    existing_customers: dict[str, Customer] = {
        c.customer_key: c
        for c in session.execute(
            select(Customer).where(Customer.customer_key.in_(incoming_keys))
        ).scalars().all()
    }

    for r in parsed:
        ck = r["customer_key"]
        if ck in existing_customers:
            c = existing_customers[ck]
            if c.first_order_date is None or r["order_date"] < c.first_order_date:
                c.first_order_date = r["order_date"]
        else:
            new_c = Customer(
                customer_key=ck,
                platform=r["platform"],
                first_order_date=r["order_date"],
            )
            session.add(new_c)
            existing_customers[ck] = new_c

    session.flush()

    incoming_order_ids = {r["order_id"] for r in parsed if r["order_id"]}
    if incoming_order_ids:
        existing_order_ids: set[str] = set(
            session.execute(
                select(Order.order_id).where(Order.order_id.in_(incoming_order_ids))
            ).scalars().all()
        )
    else:
        existing_order_ids = set()

    inserted = 0
    for r in parsed:
        oid = r["order_id"]
        if oid is not None and oid in existing_order_ids:
            continue
        session.add(Order(
            order_id=oid,
            order_date=r["order_date"],
            customer_key=r["customer_key"],
            platform=r["platform"],
            sku=r["sku"],
            quantity=r["quantity"],
            price=r["price"],
            receiver=r["receiver"],
            receiver_phone=r["receiver_phone"],
            province=r["province"],
            area=r["area"],
            full_address=r["full_address"],
            buyer_nick=r["buyer_nick"],
            coupon_name=r["coupon_name"],
            distributor=r["distributor"],
        ))
        if oid is not None:
            existing_order_ids.add(oid)
        inserted += 1

    session.commit()
    return inserted

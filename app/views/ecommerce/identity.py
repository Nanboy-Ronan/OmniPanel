# rap/app/views/ecommerce/identity.py
"""Cross-platform real-customer identity clustering (跨平台真实客户身份统一).

`Order.customer_key` means a different thing per platform — Youzan's is the
buyer's phone number, JD/Tmall's is the parsed shipping address — so the same
real person ordering from more than one platform is counted as unrelated
customers everywhere else in this codebase. This module groups orders across
platforms by phone number instead, in two confidence tiers that must never be
summed together by a caller:

  - "exact": Youzan/Tmall both export full, unmasked phone numbers, so two
    customer_keys sharing the same full phone are joined with high confidence.
  - "fuzzy": JD masks its exported phone numbers (``1******6198`` — only the
    first digit and last 4 digits survive), so JD rows can only be matched by
    that partial fingerprint. This produces real false positives (any two
    people sharing the same last 4 digits collide) and is kept structurally
    separate from the exact tier everywhere — in this module's return shape,
    in the API response, and in the UI.

Pure clustering logic lives here (no I/O) alongside the FastAPI router, since
both are small enough that splitting them into separate files (as
``app/views/media/analysis.py`` + ``routes.py`` does) would be disproportionate.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_analyst_user
from ...db import get_session
from ...db.models import Order
from ...utils.cache import analysis_cache
from ...utils.phone import (
    fuzzy_fingerprint,
    is_full_cn_mobile,
    jd_mask_fingerprint,
    normalize_phone,
)
from .analysis import _ensure_data

router = APIRouter(prefix="/analysis/identity", tags=["identity"])


# ── Pure clustering logic ───────────────────────────────────────────────────

def _new_cluster(confidence: str) -> dict:
    return {
        "confidence": confidence,
        "platforms": set(),
        "customer_keys": {},  # platform -> set[customer_key]
        "order_count": 0,
        "revenue": 0.0,
        "first_order_date": None,
        "last_order_date": None,
    }


def _merge_row_into(cluster: dict, row: dict) -> None:
    cluster["platforms"].add(row["platform"])
    cluster["customer_keys"].setdefault(row["platform"], set()).add(row["customer_key"])
    cluster["order_count"] += row["orders"]
    cluster["revenue"] += row["revenue"]
    if row["first_date"] and (cluster["first_order_date"] is None or row["first_date"] < cluster["first_order_date"]):
        cluster["first_order_date"] = row["first_date"]
    if row["last_date"] and (cluster["last_order_date"] is None or row["last_date"] > cluster["last_order_date"]):
        cluster["last_order_date"] = row["last_date"]


def _finalize_cluster(cluster_id: str, cluster: dict, attached_to: str | None = None) -> dict:
    return {
        "cluster_id": cluster_id,
        "confidence": cluster["confidence"],
        "attached_to": attached_to,
        "platforms": sorted(cluster["platforms"]),
        "customer_keys": {pf: sorted(keys) for pf, keys in cluster["customer_keys"].items()},
        "order_count": cluster["order_count"],
        "revenue": round(cluster["revenue"], 2),
        "first_order_date": cluster["first_order_date"],
        "last_order_date": cluster["last_order_date"],
    }


def build_phone_clusters(customer_rows: list[dict]) -> dict:
    """Group per-(customer_key, platform) rows into cross-platform identity clusters.

    Args:
        customer_rows: one dict per ``(customer_key, platform)`` combination,
            each with keys ``customer_key``, ``platform``, ``phone``,
            ``orders`` (int), ``revenue`` (float), ``first_date``,
            ``last_date`` (date strings or None).

    Returns:
        ``{"exact": [...], "fuzzy": [...]}`` — see module docstring for the
        confidence-tier contract. The two lists must never be combined into a
        single total by a caller; an "exact" cluster's own order/revenue
        numbers are never affected by any "fuzzy" attachment.
    """
    exact_clusters: dict[str, dict] = {}  # full phone -> cluster
    jd_rows: list[dict] = []

    # Pass 1: Youzan/Tmall rows with a full, unmasked phone build the exact tier.
    for row in customer_rows:
        if row["platform"] == "jd":
            jd_rows.append(row)
            continue
        phone = normalize_phone(row["phone"])
        if not is_full_cn_mobile(phone):
            continue  # no usable phone for this customer; not part of any cluster
        cluster = exact_clusters.setdefault(phone, _new_cluster("exact"))
        _merge_row_into(cluster, row)

    # Index exact clusters by fingerprint so JD rows can be matched against them.
    fingerprint_to_phones: dict[str, list[str]] = {}
    for phone in exact_clusters:
        fp = fuzzy_fingerprint(phone)
        if fp:
            fingerprint_to_phones.setdefault(fp, []).append(phone)

    # Pass 2: JD rows attach to exactly one matching exact cluster (fuzzy tier),
    # or — if their fingerprint matches zero or more than one exact cluster
    # (ambiguous either way) — get grouped with other JD-only rows sharing the
    # same fingerprint into a standalone fuzzy cluster.
    attached_fuzzy: dict[str, dict] = {}     # phone -> fuzzy cluster attached to it
    standalone_fuzzy: dict[str, dict] = {}   # fingerprint -> JD-only fuzzy cluster

    for row in jd_rows:
        phone = normalize_phone(row["phone"])
        fp = jd_mask_fingerprint(phone)
        if not fp:
            continue  # phone missing or not even in the expected masked shape
        matches = fingerprint_to_phones.get(fp, [])
        if len(matches) == 1:
            target_phone = matches[0]
            cluster = attached_fuzzy.setdefault(target_phone, _new_cluster("fuzzy"))
            _merge_row_into(cluster, row)
        else:
            cluster = standalone_fuzzy.setdefault(fp, _new_cluster("fuzzy"))
            _merge_row_into(cluster, row)

    exact_out = [
        _finalize_cluster(phone, cluster)
        for phone, cluster in exact_clusters.items()
    ]
    fuzzy_out = [
        _finalize_cluster(f"jd-attached:{phone}", cluster, attached_to=phone)
        for phone, cluster in attached_fuzzy.items()
    ] + [
        _finalize_cluster(f"jd-only:{fp}", cluster)
        for fp, cluster in standalone_fuzzy.items()
    ]

    return {"exact": exact_out, "fuzzy": fuzzy_out}


# ── Data access + endpoint ──────────────────────────────────────────────────

async def _fetch_customer_phone_rows(
    session: AsyncSession,
    start_date: dt.date | None,
    end_date: dt.date | None,
) -> list[dict]:
    stmt = select(
        Order.customer_key,
        Order.platform,
        func.max(Order.receiver_phone).label("phone"),
        func.count(Order.id).label("orders"),
        func.sum(Order.price).label("revenue"),
        func.min(Order.order_date).label("first_date"),
        func.max(Order.order_date).label("last_date"),
    ).group_by(Order.customer_key, Order.platform)

    if start_date:
        stmt = stmt.where(Order.order_date >= start_date)
    if end_date:
        stmt = stmt.where(Order.order_date <= end_date)

    rows = (await session.execute(stmt)).all()
    return [
        {
            "customer_key": r.customer_key,
            "platform": r.platform,
            "phone": r.phone,
            "orders": int(r.orders or 0),
            "revenue": float(r.revenue or 0.0),
            "first_date": str(r.first_date) if r.first_date else None,
            "last_date": str(r.last_date) if r.last_date else None,
        }
        for r in rows
    ]


_FUZZY_CAVEAT = (
    "京东手机号已脱敏，仅按首位数字+后四位匹配，存在误判可能；"
    "此分组结果置信度低于「精确」分组，不应与精确分组数据相加汇总。"
)


def _summarize(clusters: list[dict]) -> dict:
    return {
        "cluster_count": len(clusters),
        "total_orders": sum(c["order_count"] for c in clusters),
        "total_revenue": round(sum(c["revenue"] for c in clusters), 2),
        "clusters": clusters,
    }


@router.get("/clusters", summary="跨平台真实客户身份聚类（按手机号，analyst+）")
async def identity_clusters(
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    confidence: str | None = Query(None, pattern="^(exact|fuzzy)$"),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Cross-platform customer clusters built from ``Order.receiver_phone``.

    No ``platform`` filter — the entire point is to look across all platforms
    at once. Returns ``exact`` (Youzan/Tmall full-phone matches) and ``fuzzy``
    (JD masked-phone heuristic matches) as separate top-level buckets that are
    never combined into one number.
    """
    await _ensure_data(session)

    cache_key = analysis_cache._make_key(
        "identity_clusters",
        start_date=str(start_date) if start_date else None,
        end_date=str(end_date) if end_date else None,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is None:
        rows = await _fetch_customer_phone_rows(session, start_date, end_date)
        clusters = build_phone_clusters(rows)
        cached = {
            "exact": _summarize(clusters["exact"]),
            "fuzzy": {**_summarize(clusters["fuzzy"]), "caveat": _FUZZY_CAVEAT},
        }
        await analysis_cache.set(cache_key, cached)

    if confidence:
        return {confidence: cached[confidence]}
    return cached

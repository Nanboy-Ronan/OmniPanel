"""ETL for XHS "数据概览" JSON payloads produced by
app/collector/xhs.py::collect_xhs_overview().

Upload semantics — upsert, keyed to self-heal:
  • xhs_account_daily_metrics: one row per (account_id, metric_date).
      Every collection resubmits the platform's last 30 days, so a missed
      collection day is backfilled the next time this runs rather than
      leaving a permanent gap; a re-collected day overwrites its own row.
  • xhs_audience_source_daily: one row per
      (account_id, snapshot_date, window, source_type), snapshot_date being
      "today" at collection time — the platform exposes no daily history for
      this breakdown, only a live 7-/30-day rolling share.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import XhsAccountDailyMetric, XhsAudienceSourceDaily

_DAILY_METRIC_COLS = [
    "rise_fans_count", "loss_fans_count", "net_rise_fans_count",
    "view_count", "view_time_total_seconds", "avg_view_time_seconds",
    "home_view_count", "like_count", "collect_count", "comment_count",
    "share_count", "danmaku_count", "cover_click_rate", "video_full_view_rate",
]

_SOURCE_UPDATE_COLS = ["title", "value_pct"]

_DEDUP_CONSTRAINT_DAILY = "uq_xhs_account_daily_metrics_account_date"
_DEDUP_CONSTRAINT_SOURCE = "uq_xhs_audience_source_daily_account_date_window_source"


def _parse_date(raw) -> Optional[dt.date]:
    if isinstance(raw, dt.date):
        return raw
    try:
        return dt.date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def parse_xhs_overview_payload(payload: dict) -> tuple[list[dict], list[dict], Optional[dt.date]]:
    """Validate + normalize collect_xhs_overview()'s JSON dict into
    (daily_rows, source_rows, snapshot_date) ready for upsert. Rows with an
    unparseable date are dropped rather than raising — one bad entry should
    not sink an otherwise-good 30-day payload.

    snapshot_date is the collector's own Asia/Shanghai "as of" date (see
    collect_xhs_overview / _shanghai_today) — read from the payload rather
    than computed here, so it reflects when the data was actually observed
    regardless of what timezone this API process happens to run in. None if
    absent/unparseable; the caller's own default applies then.
    """
    snapshot_date = _parse_date(payload.get("snapshot_date"))

    daily_rows = []
    for row in payload.get("daily") or []:
        metric_date = _parse_date(row.get("metric_date"))
        if metric_date is None:
            continue
        daily_rows.append({
            "metric_date": metric_date,
            **{col: row.get(col) for col in _DAILY_METRIC_COLS},
        })

    source_rows = []
    for row in payload.get("audience_source") or []:
        window = row.get("window")
        source_type = row.get("source_type")
        if window not in ("seven", "thirty") or source_type is None:
            continue
        source_rows.append({
            # DB column is window_label, not window — `window` is a Postgres
            # reserved word (see XhsAudienceSourceDaily). The wire format
            # (collect_xhs_overview's JSON, and this function's own "window"
            # input key above) is unaffected — only the ORM-facing name here
            # changes.
            "window_label": window,
            "source_type": int(source_type),
            "title": row.get("title"),
            "value_pct": row.get("value_pct"),
        })

    return daily_rows, source_rows, snapshot_date


def upsert_xhs_daily_metrics(rows: list[dict], account_id: int, session: Session) -> int:
    """Upsert one row per (account_id, metric_date). Returns rows written."""
    if not rows:
        return 0

    from sqlalchemy import text

    rows_with_account = [{**r, "account_id": account_id} for r in rows]
    stmt = (
        pg_insert(XhsAccountDailyMetric)
        .values(rows_with_account)
        .on_conflict_do_update(
            constraint=_DEDUP_CONSTRAINT_DAILY,
            set_={col: pg_insert(XhsAccountDailyMetric).excluded[col] for col in _DAILY_METRIC_COLS}
            | {"updated_at": text("NOW()")},
        )
    )
    session.execute(stmt)
    session.commit()
    return len(rows)


def upsert_xhs_audience_source(
    rows: list[dict], account_id: int, session: Session, *, snapshot_date: dt.date | None = None,
) -> int:
    """Upsert one row per (account_id, snapshot_date, window, source_type).

    Callers should pass the collector's own Asia/Shanghai snapshot_date
    (from parse_xhs_overview_payload) explicitly. The fallback below is
    deliberately Shanghai-local too, NOT dt.date.today() (the API process's
    server-local/UTC date) — if the API host runs in UTC and the daily
    collector run lands late in the Beijing evening (early UTC the next
    day), a naive today() would silently tag every un-dated upload one day
    behind the metric_date rows collected in the same run.
    """
    if not rows:
        return 0

    from sqlalchemy import text
    from zoneinfo import ZoneInfo

    snapshot_date = snapshot_date or dt.datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    rows_with_keys = [{**r, "account_id": account_id, "snapshot_date": snapshot_date} for r in rows]
    stmt = (
        pg_insert(XhsAudienceSourceDaily)
        .values(rows_with_keys)
        .on_conflict_do_update(
            constraint=_DEDUP_CONSTRAINT_SOURCE,
            set_={col: pg_insert(XhsAudienceSourceDaily).excluded[col] for col in _SOURCE_UPDATE_COLS}
            | {"updated_at": text("NOW()")},
        )
    )
    session.execute(stmt)
    session.commit()
    return len(rows)

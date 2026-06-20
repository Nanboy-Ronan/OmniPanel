"""ETL for Xiaohongshu (小红书) xlsx exports.

XHS backend exports have this layout:
  row 0  — banner text ("最多导出排序后前1000条笔记 …")  → skip
  row 1  — real column headers                          → use as df.columns
  row 2+ — data rows

Upsert key: (title, publish_date).  Numeric traffic metrics are always
overwritten on re-upload; articles absent from the current file are kept.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import XhsPost

# Fields refreshed on every upsert (excludes dedup key + created_at)
_UPSERT_UPDATE_COLS = [
    "genre", "impressions", "views", "cover_click_rate",
    "likes", "comments", "collects", "new_followers",
    "shares", "avg_watch_time", "danmu",
]

# Dedup constraint name (must match model / migration)
_DEDUP_CONSTRAINT = "uq_xhs_posts_account_title_date"

_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def _parse_date(raw) -> Optional[date]:
    m = _DATE_RE.search(str(raw or ""))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _int_or_none(v) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None


def _float_or_none(v) -> Optional[float]:
    try:
        f = float(str(v).strip())
        return f
    except (ValueError, TypeError):
        return None


def parse_xhs_xlsx(df_raw: pd.DataFrame) -> list[dict]:
    """Parse a raw DataFrame (read with header=None) into upsert-ready dicts.

    Row 0 is the banner and is skipped.  Row 1 becomes the column headers.
    Row 2+ are the data rows.
    """
    # Row 1 holds the real headers; data starts at row 2
    headers = [str(h).strip() for h in df_raw.iloc[1].tolist()]
    df = df_raw.iloc[2:].copy()
    df.columns = headers
    df = df.reset_index(drop=True)

    rows = []
    for _, row in df.iterrows():
        title = str(row.get("笔记标题", "") or "").strip()
        if not title:
            continue
        pub_date = _parse_date(row.get("首次发布时间"))
        if pub_date is None:
            continue
        rows.append({
            "title": title,
            "publish_date": pub_date,
            "genre": str(row.get("体裁", "") or "").strip() or None,
            "impressions": _int_or_none(row.get("曝光")),
            "views": _int_or_none(row.get("观看量")),
            "cover_click_rate": _float_or_none(row.get("封面点击率")),
            "likes": _int_or_none(row.get("点赞")),
            "comments": _int_or_none(row.get("评论")),
            "collects": _int_or_none(row.get("收藏")),
            "new_followers": _int_or_none(row.get("涨粉")),
            "shares": _int_or_none(row.get("分享")),
            "avg_watch_time": _float_or_none(row.get("人均观看时长")),
            "danmu": _int_or_none(row.get("弹幕")),
        })
    return rows


def upsert_xhs_posts(rows: list[dict], account_id: int, session: Session) -> dict:
    """Upsert parsed rows (with account_id) into xhs_posts.  Returns summary counts."""
    if not rows:
        return {"total": 0, "upserted": 0}

    from sqlalchemy import text

    rows_with_account = [{**r, "account_id": account_id} for r in rows]

    stmt = (
        pg_insert(XhsPost)
        .values(rows_with_account)
        .on_conflict_do_update(
            constraint=_DEDUP_CONSTRAINT,
            set_={col: pg_insert(XhsPost).excluded[col] for col in _UPSERT_UPDATE_COLS}
            | {"updated_at": text("NOW()")},
        )
    )
    session.execute(stmt)
    session.commit()
    return {"total": len(rows), "upserted": len(rows)}

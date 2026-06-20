"""ETL for Zhihu (知乎) CSV exports.

Both article and QA exports share the same layout (CSV, UTF-8 BOM):
  row 0 = column headers
  row 1+ = data rows

Article columns: 标题, 发布时间, 链接, 阅读, 点赞, 喜欢, 评论, 收藏, 分享
QA columns:      标题, 发布时间, 链接, 阅读, 播放, 点赞, 喜欢, 评论, 收藏, 分享

Date format: YYYY-MM-DD
Upsert key: (content_type, title, publish_date)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import ZhihuPost

_UPSERT_UPDATE_COLS = [
    "url", "reads", "plays", "likes", "favorites",
    "comments", "collects", "shares",
]

_DEDUP_CONSTRAINT = "uq_zhihu_posts_type_title_date"

VALID_CONTENT_TYPES = {"article", "qa"}


def _parse_zhihu_date(raw) -> Optional[date]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _int_or_none(v) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None


def parse_zhihu_csv(df: pd.DataFrame, content_type: str) -> list[dict]:
    """Parse a Zhihu export DataFrame into upsert-ready dicts.

    The DataFrame should have its column headers as the first row (already
    consumed by pd.read_csv).  Rows with empty titles or unparseable dates
    are silently skipped.
    """
    rows = []
    for _, row in df.iterrows():
        title = str(row.get("标题", "") or "").strip()
        if not title:
            continue
        pub_date = _parse_zhihu_date(row.get("发布时间"))
        if pub_date is None:
            continue
        url = str(row.get("链接", "") or "").strip() or None
        rows.append({
            "content_type": content_type,
            "title": title,
            "publish_date": pub_date,
            "url": url,
            "reads": _int_or_none(row.get("阅读")),
            "plays": _int_or_none(row.get("播放")) if content_type == "qa" else None,
            "likes": _int_or_none(row.get("点赞")),
            "favorites": _int_or_none(row.get("喜欢")),
            "comments": _int_or_none(row.get("评论")),
            "collects": _int_or_none(row.get("收藏")),
            "shares": _int_or_none(row.get("分享")),
        })
    return rows


def upsert_zhihu_posts(rows: list[dict], session: Session) -> dict:
    """Upsert parsed rows into zhihu_posts.  Returns summary counts."""
    if not rows:
        return {"total": 0, "upserted": 0}

    from sqlalchemy import text

    stmt = (
        pg_insert(ZhihuPost)
        .values(rows)
        .on_conflict_do_update(
            constraint=_DEDUP_CONSTRAINT,
            set_={col: pg_insert(ZhihuPost).excluded[col] for col in _UPSERT_UPDATE_COLS}
            | {"updated_at": text("NOW()")},
        )
    )
    session.execute(stmt)
    session.commit()
    return {"total": len(rows), "upserted": len(rows)}

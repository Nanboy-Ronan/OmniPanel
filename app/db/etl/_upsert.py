"""Shared helpers for the media-export upserts (xhs / zhihu / pgy / channels)."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import func


def coalesce_update_set(insert_stmt, model, columns: Iterable[str]) -> dict:
    """ON CONFLICT SET clause that keeps the stored value when the incoming
    one is NULL: ``col = COALESCE(excluded.col, <table>.col)``.

    Exports are re-uploaded daily and only ever *add* information. A NULL in
    an upload means "not present in this file" (a renamed/removed export
    column, a JSON source that lacks the field, a '-' placeholder) — never
    "the metric is now unknown" — so it must not erase history.
    """
    table = model.__table__
    return {col: func.coalesce(insert_stmt.excluded[col], table.c[col]) for col in columns}


def require_columns(headers: Iterable[str], required: Iterable[str], source: str) -> None:
    """Raise ValueError naming every required export column missing from
    *headers*, so a renamed column fails the import instead of silently
    writing NULL for that metric on every row."""
    present = {str(h).strip() for h in headers}
    missing = [c for c in required if c not in present]
    if missing:
        raise ValueError(
            f"{source}导出文件缺少必需列：{'、'.join(missing)}。"
            "平台可能更改了导出列名，请检查文件或更新解析规则后再导入。"
        )


_SNAPSHOT_FIXED = {"id", "post_id", "captured_on", "source_file_id", "created_at"}


def snapshot_metric_columns(snapshot_model) -> list[str]:
    return [c.name for c in snapshot_model.__table__.columns if c.name not in _SNAPSHOT_FIXED]


def capture_day():
    """Today in the app timezone (the DB runs in UTC)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ...config import settings
    return datetime.now(ZoneInfo(settings.app_timezone)).date()


def write_snapshots(session, snapshot_model, returned, rows, key, *, captured_on=None, source_file_id=None) -> int:
    """Record today's as-uploaded metrics for each upserted post.

    *returned* are the (id, *key) rows from the post upsert's RETURNING;
    *rows* are the parsed upload rows. Values come from the upload itself, not
    the COALESCEd post row, so a metric missing from this file stays NULL here.
    Re-uploading on the same day replaces that day's snapshot.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    ids = {tuple(r[1:]): r[0] for r in returned}
    metrics = snapshot_metric_columns(snapshot_model)
    day = captured_on or capture_day()
    values = []
    for row in rows:
        post_id = ids.get(tuple(row.get(k) for k in key))
        if post_id is not None:
            values.append({"post_id": post_id, "captured_on": day, "source_file_id": source_file_id,
                           **{m: row.get(m) for m in metrics}})
    if len(values) < len(rows):
        logging.getLogger(__name__).warning(
            "snapshot_rows_unmatched table=%s matched=%d rows=%d",
            snapshot_model.__tablename__, len(values), len(rows))
    if not values:
        return 0
    insert = pg_insert(snapshot_model).values(values)
    session.execute(insert.on_conflict_do_update(
        index_elements=["post_id", "captured_on"],
        set_={c: insert.excluded[c] for c in [*metrics, "source_file_id"]},
    ))
    return len(values)

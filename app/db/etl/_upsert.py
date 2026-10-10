"""Shared helpers for the media-export upserts (xhs / zhihu / pgy / channels)."""
from __future__ import annotations

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

"""app/db/media_etl.py — WeChat Official Account xlsx export parser.

# DISABLED 2026-06-01: xlsx upload was never used in production; data comes
# from manual API sync (POST /media/wechat/sync).  All code below is commented
# out.  See README §"Disabled: WeChat xlsx upload" to re-enable.
"""
from __future__ import annotations

# import hashlib   # disabled xlsx parser
# import re        # disabled xlsx parser
# from datetime import date, datetime  # disabled xlsx parser
# from pathlib import Path             # disabled xlsx parser
# from typing import Any               # disabled xlsx parser
# import pandas as pd                  # disabled xlsx parser

# ── Column name → internal key mapping (disabled) ─────────────────────────────
# _COL_MAP: dict[str, str] = {
#     "文章名称":        "title",
#     "发布日期":        "publish_date",
#     "阅读人数":        "read_user_count",
#     "阅读（次）":      "read_count",
#     "点赞人数":        "like_user",
#     "分享人数":        "share_user_count",
#     "留言条数":        "comment_count",
#     "划线人数":        "collection_user",
#     "平均阅读时长（分钟）": "read_avg_time",
# }
# _SKIP_COLS = {"发布时间", "推送", "听全文", "服务号是否转载"}
# _FUWUHAO_PREFIX = "服务号-"
# _TITLE_COL = "文章名称"
# _DATE_PATTERNS = [
#     (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})-\d{1,2}:\d{2}$"), "%Y/%m/%d"),
#     (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), "%Y/%m/%d"),
#     (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), "%Y-%m-%d"),
# ]


# def _parse_date(raw: Any) -> date | None:
#     if raw is None:
#         return None
#     if isinstance(raw, datetime):
#         return raw.date()
#     if isinstance(raw, date):
#         return raw
#     text = str(raw).strip()
#     if not text:
#         return None
#     for pattern, fmt in _DATE_PATTERNS:
#         m = pattern.match(text)
#         if m:
#             date_part = text[:10].replace("/", "-")
#             try:
#                 return datetime.strptime(date_part, "%Y-%m-%d").date()
#             except ValueError:
#                 continue
#     try:
#         return pd.to_datetime(text).date()
#     except Exception:
#         return None


# def _to_int(val: Any) -> int:
#     try:
#         if val is None or (isinstance(val, float) and __import__("math").isnan(val)):
#             return 0
#         return int(float(str(val)))
#     except (ValueError, TypeError):
#         return 0


# def _to_float_or_none(val: Any) -> float | None:
#     try:
#         if val is None or (isinstance(val, float) and __import__("math").isnan(val)):
#             return None
#         f = float(str(val))
#         return f if not __import__("math").isnan(f) else None
#     except (ValueError, TypeError):
#         return None


# def _load_first_data_sheet(filepath: str | Path) -> pd.DataFrame | None:
#     path = str(filepath)
#     def _try_read(engine: str) -> pd.DataFrame | None:
#         try:
#             xl = pd.ExcelFile(path, engine=engine)
#             for sheet in xl.sheet_names:
#                 df = pd.read_excel(xl, sheet_name=sheet, dtype=str)
#                 if _TITLE_COL in df.columns:
#                     return df
#         except Exception:
#             pass
#         return None
#     df = _try_read("calamine")
#     if df is None:
#         df = _try_read("openpyxl")
#     return df


# def parse_wechat_xlsx(
#     filepath: str | Path,
#     account_id: int,
# ) -> tuple[list[dict[str, Any]], list[str]]:
#     df = _load_first_data_sheet(filepath)
#     if df is None:
#         return [], ["找不到包含「文章名称」列的 sheet"]
#     df = df.dropna(how="all")
#     rows: list[dict[str, Any]] = []
#     rejected: list[str] = []
#     for idx, raw_row in df.iterrows():
#         row_dict = raw_row.to_dict()
#         title_raw = row_dict.get("文章名称")
#         if title_raw is None or str(title_raw).strip() in ("", "nan", "None"):
#             rejected.append(f"行 {idx + 2}: 文章名称为空")
#             continue
#         title = str(title_raw).strip()
#         pub_date = _parse_date(row_dict.get("发布日期"))
#         if pub_date is None:
#             rejected.append(f"行 {idx + 2}: 「{title[:20]}」发布日期无法解析 ({row_dict.get('发布日期')!r})")
#             continue
#         external_id = hashlib.sha256(f"{account_id}:{title}".encode()).hexdigest()[:32]
#         read_user_count = _to_int(row_dict.get("阅读人数"))
#         read_count      = _to_int(row_dict.get("阅读（次）"))
#         like_user       = _to_int(row_dict.get("点赞人数"))
#         share_user      = _to_int(row_dict.get("分享人数"))
#         comment_count   = _to_int(row_dict.get("留言条数"))
#         collection_user = _to_int(row_dict.get("划线人数"))
#         read_avg_time   = _to_float_or_none(row_dict.get("平均阅读时长（分钟）"))
#         mapped_keys = set(_COL_MAP.keys()) | _SKIP_COLS
#         raw_payload: dict[str, Any] = {}
#         for col, val in row_dict.items():
#             if col in mapped_keys:
#                 continue
#             if str(col).startswith(_FUWUHAO_PREFIX):
#                 continue
#             if val is None or (isinstance(val, float) and __import__("math").isnan(val)):
#                 continue
#             raw_payload[col] = val
#         rows.append({
#             "external_id": external_id, "title": title, "publish_date": pub_date,
#             "read_user_count": read_user_count, "read_count": read_count,
#             "like_user": like_user, "share_user_count": share_user,
#             "comment_count": comment_count, "collection_user": collection_user,
#             "read_avg_time": read_avg_time, "raw_payload": raw_payload,
#         })
#     return rows, rejected

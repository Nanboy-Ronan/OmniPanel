"""ETL for WeChat Channels (视频号) exports.

Two independent input formats, both verified live against a real 视频号
account, feeding the same upsert:

1. **JSON API** (parse_channels_api_json) — the collector's primary path.
   视频号助手's "下载表格" button (数据中心 → 视频数据 → 单篇视频) turns out to
   be client-side sugar over a plain authenticated JSON endpoint:
   `POST /micro/statistic/cgi-bin/mmfinderassistant-bin/statistic/
   download_post_data` with a `{startTime, endTime}` unix-seconds body,
   called from within the `micro/statistic/post` iframe's origin (see
   app/collector/channels.py). Calling it directly with a wide range
   sidesteps the calendar widget entirely (no fragile date-picker
   automation, no per-request "近7天" default) **and** returns full account
   history in one shot — confirmed live: the whole back catalog to the
   account's first-ever post, in a single call, vs. 1 video from the UI's
   default "近7天" filter. This is the preferred path; the CSV path below
   exists for admins who manually download and upload the file themselves.

2. **CSV file** (parse_channels_xlsx) — what a human gets by actually
   clicking "下载表格" (NOT "导出" — every other collected platform uses
   that word; 视频号助手 alone doesn't) and uploading it through
   /media/channels/upload. UTF-8-BOM, comma-separated, one row per video,
   header on row 0 (no banner row, no merged header groups). Sample kept at
   data/channels_example.csv.
     视频描述,视频ID,发布时间,完播率,平均播放时长,播放量,推荐,喜欢,评论量,分享量,
     关注量,转发聊天和朋友圈,设为铃声,设为状态,设为朋友圈封面,企微链接点击次数,
     企微链接点击人数,添加到通讯录次数,添加到通讯录人数

The two formats were cross-checked field-by-field against the same video
and agree exactly (播放量=readCount, 推荐=likeCount, 喜欢=favCount,
评论量=commentCount, 分享量=forwardCount, 关注量=followCount,
完播率=fullPlayRate, 平均播放时长=avgPlayTimeSec) with one real gap: the
JSON API has no 企微链接点击/添加到通讯录 fields at all (grep'd a full
account-wide response, present nowhere) — those four columns are CSV-only,
and stay None on the JSON path. Sample kept at data/channels_example.json.

Notable surprises vs. the pre-verification guess (kept here so nobody
re-derives this the hard way):
  - There is a real, stable video_id (视频ID / objectId) — no need for the
    sha256(title+date) dedup hack XhsPost-style platforms need; this uses
    it directly, same as pgy_notes.note_id.
  - No 曝光 (exposure) or 收藏 (favorites) columns exist for 视频号 videos.
  - "Likes" is actually two distinct signals: 推荐/likeCount (the ♡ icon in
    the UI) and 喜欢/favCount (the 👍 icon) — not interchangeable, both kept
    as separate columns (despite the JSON field literally being named
    "favCount" — it is NOT a collection/bookmark count here).
  - JSON's createTime is a unix timestamp that must be read in **China time
    (Asia/Shanghai, UTC+8)**, not the collector host's local timezone — a
    naive local conversion was observed to land a video on the wrong
    calendar day (off by one) during verification.
  - 平均播放时长 in the CSV is a string like "14.73秒" (seconds suffix), not
    a bare number; the JSON's avgPlayTimeSec is already a bare float.
  - 完播率 in the CSV is a percent string like "4.41%", normalized to a 0-1
    fraction; the JSON's fullPlayRate is already a 0-1 fraction.
  - The CSV's single "转发聊天和朋友圈" column is, in the JSON API, actually
    two separate counters summed together (forwardAllChatCount +
    forwardSnsCount) — the JSON path is the more granular of the two, but
    this module folds them back into one column to match the CSV schema.
  - A cluster of WeCom-integration engagement actions has no equivalent on
    any other collected platform: 转发聊天和朋友圈, 设为铃声/状态/朋友圈封面,
    企微链接点击(次数/人数), 添加到通讯录(次数/人数).

Upload semantics — ADDITIVE ONLY (never deletes rows), same as xhs.py:
  • New video (video_id not yet in DB for this account) → INSERT.
  • Existing video → UPDATE traffic metrics only; created_at untouched.
  • Video absent from the current file but present in DB → left unchanged.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import WxChannelsPost

# China Standard Time — createTime (unix seconds) must be read in this zone,
# not the collector host's local time. See module docstring.
_CST = timezone(timedelta(hours=8))

_DEDUP_CONSTRAINT = "uq_wx_channels_posts_account_video_id"

_UPSERT_UPDATE_COLS = [
    "title", "publish_date", "plays", "recommends", "likes_thumb", "comments",
    "shares", "new_fans", "forwards_chat_moments", "set_as_ringtone",
    "set_as_status", "set_as_moments_cover", "wecom_link_clicks",
    "wecom_link_click_users", "added_to_contacts", "added_to_contacts_users",
    "avg_watch_duration", "completion_rate", "raw_payload",
]

# field name -> real export header (verified — see module docstring)
_COLUMNS = {
    "title": "视频描述",
    "video_id": "视频ID",
    "publish_date": "发布时间",
    "completion_rate": "完播率",
    "avg_watch_duration": "平均播放时长",
    "plays": "播放量",
    "recommends": "推荐",
    "likes_thumb": "喜欢",
    "comments": "评论量",
    "shares": "分享量",
    "new_fans": "关注量",
    "forwards_chat_moments": "转发聊天和朋友圈",
    "set_as_ringtone": "设为铃声",
    "set_as_status": "设为状态",
    "set_as_moments_cover": "设为朋友圈封面",
    "wecom_link_clicks": "企微链接点击次数",
    "wecom_link_click_users": "企微链接点击人数",
    "added_to_contacts": "添加到通讯录次数",
    "added_to_contacts_users": "添加到通讯录人数",
}
_INT_FIELDS = [
    "plays", "recommends", "likes_thumb", "comments", "shares", "new_fans",
    "forwards_chat_moments", "set_as_ringtone", "set_as_status",
    "set_as_moments_cover", "wecom_link_clicks", "wecom_link_click_users",
    "added_to_contacts", "added_to_contacts_users",
]

_DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")
_SECONDS_SUFFIX_RE = re.compile(r"秒\s*$")


def _parse_date(raw) -> Optional[date]:
    m = _DATE_RE.search(str(raw or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _int_or_none(v) -> Optional[int]:
    """No '%' or unit-suffix stripping here — none of the int fields carry
    one in the verified export. A stray suffix landing here (e.g. a future
    export format change) should yield None, a visible gap, not a silently
    truncated wrong number."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s or s == "-":
        return None
    try:
        return int(float(s.replace(",", "")))
    except (ValueError, TypeError):
        return None


def _float_or_none(v) -> Optional[float]:
    """Handles the two unit-suffixed formats seen in the verified export:
    a trailing '%' (completion_rate, e.g. "4.41%") normalized to a 0-1
    fraction, and a trailing '秒' (avg_watch_duration, e.g. "14.73秒")
    stripped to a bare float."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s or s == "-":
        return None
    try:
        if s.endswith("%"):
            return float(s[:-1].replace(",", "")) / 100.0
        s = _SECONDS_SUFFIX_RE.sub("", s)
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _str_or_none(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s if s else None


def parse_channels_xlsx(df_raw: pd.DataFrame, account_id: int) -> list[dict]:
    """Parse a raw DataFrame (read with header=None) into upsert-ready dicts.

    Header is row 0, data is row 1+ — the real export has no banner row and
    no merged header groups.
    """
    if df_raw.empty:
        return []

    headers = [str(h).strip() for h in df_raw.iloc[0].tolist()]
    df = df_raw.iloc[1:].copy()
    df.columns = headers
    df = df.reset_index(drop=True)

    title_col = _COLUMNS["title"]
    id_col = _COLUMNS["video_id"]
    if title_col not in headers or id_col not in headers:
        return []

    rows = []
    for _, row in df.iterrows():
        video_id = _str_or_none(row.get(id_col))
        if not video_id:
            continue
        title = str(row.get(title_col, "") or "").strip()
        if not title:
            continue

        entry = {
            "video_id": video_id,
            "title": title,
            "publish_date": _parse_date(row.get(_COLUMNS["publish_date"])),
            "completion_rate": _float_or_none(row.get(_COLUMNS["completion_rate"])),
            "avg_watch_duration": _float_or_none(row.get(_COLUMNS["avg_watch_duration"])),
            "raw_payload": {k: (None if pd.isna(v) else str(v)) for k, v in row.items()},
        }
        for field in _INT_FIELDS:
            entry[field] = _int_or_none(row.get(_COLUMNS[field]))
        rows.append(entry)
    return rows


def parse_channels_api_json(raw: bytes | str, account_id: int) -> list[dict]:
    """Parse a raw download_post_data JSON API response into upsert-ready dicts.

    See module docstring for the field mapping this was verified against
    (video_id=objectId, plays=readCount, recommends=likeCount,
    likes_thumb=favCount, comments=commentCount, shares=forwardCount,
    new_fans=followCount, completion_rate=fullPlayRate,
    avg_watch_duration=avgPlayTimeSec, forwards_chat_moments=
    forwardAllChatCount+forwardSnsCount, set_as_ringtone=ringsetCount,
    set_as_status=statusrefCount, set_as_moments_cover=snscoverCount).
    wecom_link_* / added_to_contacts_* are always None here — not present in
    this API's response shape at all (only the CSV export has them).
    """
    try:
        payload = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    except (json.JSONDecodeError, TypeError):
        return []

    videos = (payload.get("data") or {}).get("list") or []
    if not videos:
        return []

    rows = []
    for v in videos:
        video_id = v.get("objectId") or v.get("exportId")
        if not video_id:
            continue
        # Unlike the CSV path (where an empty title usually means a stray
        # blank/header row), a real video here can legitimately have no
        # caption — verified live: a real account can have a video with an
        # empty description and status=1 (not deleted). Keep it; only
        # video_id (checked above) is required.
        title = ((v.get("desc") or {}).get("description") or "").strip()

        create_ts = v.get("createTime")
        publish_date = (
            datetime.fromtimestamp(create_ts, tz=_CST).date()
            if isinstance(create_ts, (int, float)) and create_ts > 0
            else None
        )

        rows.append({
            "video_id": video_id,
            "title": title,
            "publish_date": publish_date,
            "plays": v.get("readCount"),
            "recommends": v.get("likeCount"),
            "likes_thumb": v.get("favCount"),
            "comments": v.get("commentCount"),
            "shares": v.get("forwardCount"),
            "new_fans": v.get("followCount"),
            "forwards_chat_moments": (v.get("forwardAllChatCount") or 0) + (v.get("forwardSnsCount") or 0),
            "set_as_ringtone": v.get("ringsetCount"),
            "set_as_status": v.get("statusrefCount"),
            "set_as_moments_cover": v.get("snscoverCount"),
            "wecom_link_clicks": None,
            "wecom_link_click_users": None,
            "added_to_contacts": None,
            "added_to_contacts_users": None,
            "avg_watch_duration": v.get("avgPlayTimeSec"),
            "completion_rate": v.get("fullPlayRate"),
            # Trimmed: v["desc"]["media"] carries signed, expiring CDN URLs
            # (video/thumbnail download tokens) that bloat storage for no
            # lasting value — drop just that key, keep everything else.
            "raw_payload": {**v, "desc": {k: val for k, val in (v.get("desc") or {}).items() if k != "media"}},
        })
    return rows


def upsert_channels_posts(rows: list[dict], account_id: int, session: Session) -> dict:
    """Insert new videos and update existing ones; never remove videos absent from rows.

    Matched by video_id (real, stable — see module docstring). On a match
    the columns in _UPSERT_UPDATE_COLS are overwritten;
    id/account_id/video_id/created_at are left as-is. Rows already in the
    DB not present in `rows` are untouched.
    """
    if not rows:
        return {"total": 0, "upserted": 0}

    rows_with_account = [{**r, "account_id": account_id} for r in rows]

    stmt = (
        pg_insert(WxChannelsPost)
        .values(rows_with_account)
        .on_conflict_do_update(
            constraint=_DEDUP_CONSTRAINT,
            set_={col: pg_insert(WxChannelsPost).excluded[col] for col in _UPSERT_UPDATE_COLS}
            | {"updated_at": text("NOW()")},
        )
    )
    session.execute(stmt)
    session.commit()
    return {"total": len(rows), "upserted": len(rows)}

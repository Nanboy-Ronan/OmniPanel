"""Unit and integration tests for WeChat Channels (视频号) ETL and API endpoints.

Verified live against a real export from a 视频号 account (see
app/db/etl/channels.py's module docstring). The sample data below is built
inline with synthetic values, mirroring that exact layout — same convention
as tests/test_pgy.py's `_make_pgy_xlsx_bytes()` — so no real export file
needs to be tracked in the repo. Two independent input formats are tested —
the JSON API (collector's primary path, confirmed to return full account
history in one call) and the CSV a human gets from manually clicking "下载
表格" (capped to whatever date-range filter was active — see that module
for why the collector doesn't use this path anymore).
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from app.db.etl.channels import (
    _float_or_none,
    _int_or_none,
    parse_channels_api_json,
    parse_channels_xlsx,
)
from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401

# The one video shared by both synthetic samples below, so the cross-format
# consistency check has something to compare.
_SHARED_VIDEO_ID = "export/UzFfBgAAxOOgWDk7RwySjMzT4DCaK42Y3QBKOFW-O1qJ9TXaDA"


def _make_channels_csv_df() -> pd.DataFrame:
    """Build a one-row DataFrame with the real (verified) CSV column order —
    same layout as data/channels_example.csv, header=None like the real
    upload path reads it."""
    header = [
        "视频描述", "视频ID", "发布时间", "完播率", "平均播放时长", "播放量",
        "推荐", "喜欢", "评论量", "分享量", "关注量", "转发聊天和朋友圈",
        "设为铃声", "设为状态", "设为朋友圈封面", "企微链接点击次数",
        "企微链接点击人数", "添加到通讯录次数", "添加到通讯录人数",
    ]
    row = [
        "示例品牌 两周年｜感谢一路支持\n#示例品牌 #示例话题\n",
        _SHARED_VIDEO_ID,
        "2026/08/07",
        "4.41%",
        "14.73秒",
        "1817",
        "42",
        "33",
        "5",
        "25",
        "9",
        "25",
        "0",
        "0",
        "0",
        "",
        "",
        "",
        "",
    ]
    return pd.DataFrame([header, row])


def _make_channels_api_payload() -> dict:
    """Build a 3-video API payload — same shape as data/channels_example.json,
    with the first video matching _make_channels_csv_df()'s row exactly (same
    video_id and metrics) so the two formats can be cross-checked."""
    return {
        "errCode": 0,
        "errMsg": "request successful",
        "data": {
            "list": [
                {
                    "commentList": [],
                    "objectId": _SHARED_VIDEO_ID,
                    "createTime": 1786147681,
                    "likeCount": 42,
                    "commentCount": 5,
                    "readCount": 1817,
                    "forwardCount": 25,
                    "favCount": 33,
                    "commentClose": 0,
                    "visibleType": 1,
                    "status": 1,
                    "desc": {
                        "description": "示例品牌 两周年｜感谢一路支持\n#示例品牌 #示例话题\n",
                        "mediaType": 4,
                    },
                    "objectType": 0,
                    "fullPlayRate": 0.04405286343612335,
                    "avgPlayTimeSec": 14.734030837004406,
                    "ringsetCount": 0,
                    "snscoverCount": 0,
                    "statusrefCount": 0,
                    "forwardAggregationCount": 25,
                    "followCount": 9,
                    "forwardSnsCount": 7,
                    "forwardAllChatCount": 18,
                    "deleteTime": 0,
                },
                {
                    "commentList": [],
                    "objectId": "export/UzFfBgAAxOCgVFMYICCIjMzT4DCa9AF28D7jb3ffdoaXQiOFsw",
                    "createTime": 1785495601,
                    "likeCount": 8,
                    "commentCount": 1,
                    "readCount": 625,
                    "forwardCount": 0,
                    "favCount": 7,
                    "status": 1,
                    "desc": {
                        "description": "这是一条示例科普内容，用于演示导出格式，不代表真实产品信息。\n#示例科普 #示例话题",
                        "mediaType": 4,
                    },
                    "fullPlayRate": 0.03514376996805112,
                    "avgPlayTimeSec": 13.130990415335463,
                    "ringsetCount": 0,
                    "snscoverCount": 0,
                    "statusrefCount": 0,
                    "followCount": 1,
                    "forwardSnsCount": 0,
                    "forwardAllChatCount": 0,
                    "deleteTime": 0,
                },
                {
                    "commentList": [],
                    "objectId": "export/UzFfBgAAxJWgHBpiexarjMzT4DCaBBYv26CvbWy0WmvYiS6BTg",
                    "createTime": 1783390895,
                    "likeCount": 2,
                    "commentCount": 0,
                    "readCount": 217,
                    "forwardCount": 1,
                    "favCount": 2,
                    "status": 1,
                    "desc": {
                        "description": "这是另一条示例内容，仅用于测试字段解析，不含真实业务信息。\n#示例内容",
                        "mediaType": 4,
                    },
                    "fullPlayRate": 0.009216589861751152,
                    "avgPlayTimeSec": 6.714285714285714,
                    "ringsetCount": 0,
                    "snscoverCount": 0,
                    "statusrefCount": 0,
                    "followCount": 0,
                    "forwardSnsCount": 0,
                    "forwardAllChatCount": 1,
                    "deleteTime": 0,
                },
            ]
        },
    }


def _make_channels_api_bytes() -> bytes:
    return json.dumps(_make_channels_api_payload()).encode("utf-8")


# ── CSV path ─────────────────────────────────────────────────────────────

def test_parse_channels_xlsx_with_sample_data():
    rows = parse_channels_xlsx(_make_channels_csv_df(), account_id=1)

    assert len(rows) == 1, f"Expected 1 parsed row, got {len(rows)}"

    row0 = rows[0]
    assert row0["video_id"] == _SHARED_VIDEO_ID
    assert "示例品牌" in row0["title"]
    assert row0["publish_date"] == date(2026, 8, 7)
    assert row0["plays"] == 1817
    assert row0["recommends"] == 42
    assert row0["likes_thumb"] == 33
    assert row0["comments"] == 5
    assert row0["shares"] == 25
    assert row0["new_fans"] == 9
    assert row0["forwards_chat_moments"] == 25
    assert row0["set_as_ringtone"] == 0
    assert row0["set_as_status"] == 0
    assert row0["set_as_moments_cover"] == 0
    # These columns are blank (no WeCom link on this video) -> None, not 0.
    assert row0["wecom_link_clicks"] is None
    assert row0["wecom_link_click_users"] is None
    assert row0["added_to_contacts"] is None
    assert row0["added_to_contacts_users"] is None
    # "4.41%" -> 0.0441 (fraction)
    assert row0["completion_rate"] == pytest.approx(0.0441)
    # "14.73秒" -> 14.73 (秒 suffix stripped)
    assert row0["avg_watch_duration"] == pytest.approx(14.73)


def test_parse_channels_xlsx_empty():
    assert parse_channels_xlsx(pd.DataFrame(), account_id=1) == []


def test_parse_channels_xlsx_missing_columns_returns_empty():
    df_raw = pd.DataFrame([["日期", "播放量"], ["2026-07-01", "100"]])
    assert parse_channels_xlsx(df_raw, account_id=1) == []


def test_float_or_none_percent_and_seconds_suffix():
    assert _float_or_none("4.41%") == pytest.approx(0.0441)
    assert _float_or_none("14.73秒") == pytest.approx(14.73)
    assert _float_or_none("-") is None
    assert _float_or_none(None) is None


def test_int_or_none_rejects_percent_string():
    """A percent string landing in an int column (e.g. a wrong alias
    mapping) must yield None, not a silently-truncated wrong number."""
    assert _int_or_none("45%") is None
    assert _int_or_none("1,234") == 1234


# ── JSON API path (collector's primary path) ────────────────────────────

def test_parse_channels_api_json_with_sample_data():
    rows = parse_channels_api_json(_make_channels_api_bytes(), account_id=1)

    assert len(rows) == 3, f"Expected 3 parsed rows, got {len(rows)}"

    row0 = rows[0]
    assert row0["video_id"] == _SHARED_VIDEO_ID
    assert "示例品牌" in row0["title"]
    # createTime is read in China time (UTC+8), not host local time — see
    # module docstring for why a naive conversion landed on the wrong day.
    assert row0["publish_date"] == date(2026, 8, 8)
    assert row0["plays"] == 1817
    assert row0["recommends"] == 42
    assert row0["likes_thumb"] == 33
    assert row0["comments"] == 5
    assert row0["shares"] == 25
    assert row0["new_fans"] == 9
    assert row0["set_as_ringtone"] == 0
    # avgPlayTimeSec / fullPlayRate arrive as native floats — no string parsing.
    assert row0["completion_rate"] == pytest.approx(0.04405286343612335)
    assert row0["avg_watch_duration"] == pytest.approx(14.734030837004406)
    # Not present in this API's response shape at all (CSV-only fields).
    assert row0["wecom_link_clicks"] is None
    assert row0["added_to_contacts"] is None


def test_parse_channels_api_json_cross_checks_against_csv_for_same_video():
    """The two independent input formats must agree on the one video both
    samples share — this is the actual verification, not just a fixture
    smoke test."""
    csv_rows = parse_channels_xlsx(_make_channels_csv_df(), account_id=1)
    json_rows = parse_channels_api_json(_make_channels_api_bytes(), account_id=1)

    csv_row = next(r for r in csv_rows if r["video_id"] == _SHARED_VIDEO_ID)
    json_row = next(r for r in json_rows if r["video_id"] == _SHARED_VIDEO_ID)

    for field in ("plays", "recommends", "likes_thumb", "comments", "shares", "new_fans"):
        assert csv_row[field] == json_row[field], f"{field} disagrees: csv={csv_row[field]} json={json_row[field]}"
    assert csv_row["completion_rate"] == pytest.approx(json_row["completion_rate"], abs=0.001)
    assert csv_row["avg_watch_duration"] == pytest.approx(json_row["avg_watch_duration"], abs=0.01)


def test_parse_channels_api_json_empty():
    assert parse_channels_api_json(b'{"errCode":0,"data":{"list":[]}}', account_id=1) == []
    assert parse_channels_api_json(b'not json', account_id=1) == []


def test_parse_channels_api_json_keeps_video_with_empty_title():
    """A real video can have no caption (verified live: an account's video
    can have status=1, deleteTime=0, and an empty description). Only
    video_id is required to keep a row — dropping on empty title would
    silently lose real data."""
    payload = {
        "errCode": 0,
        "data": {
            "list": [
                {
                    "objectId": "export/no-title-video",
                    "createTime": 1754497299,
                    "desc": {"description": ""},
                    "readCount": 10,
                }
            ]
        },
    }
    rows = parse_channels_api_json(json.dumps(payload).encode("utf-8"), account_id=1)
    assert len(rows) == 1
    assert rows[0]["video_id"] == "export/no-title-video"
    assert rows[0]["title"] == ""


# ── API round-trip ────────────────────────────────────────────────────────

def test_channels_api_upload_and_query(client, tokens):
    # 1. Create a WeChat Channels account
    r_create = client.post(
        "/media/channels/accounts",
        json={"name": "视频号测试账号"},
        headers=_auth(tokens["admin"]),
    )
    assert r_create.status_code == 201
    acc_id = r_create.json()["id"]

    # 2. Upload the sample JSON (the collector's actual output format)
    file_bytes = _make_channels_api_bytes()

    r_upload = client.post(
        "/media/channels/upload",
        data={"account_id": acc_id},
        files={"file": ("channels_video_data.json", file_bytes, "application/json")},
        headers=_auth(tokens["admin"]),
    )
    assert r_upload.status_code == 200
    upload_res = r_upload.json()
    assert upload_res["total"] == 3
    assert upload_res["upserted"] == 3

    # 3. Query posts
    r_posts = client.get(
        f"/media/channels/posts?account_id={acc_id}",
        headers=_auth(tokens["analyst"]),
    )
    assert r_posts.status_code == 200
    posts = r_posts.json()
    assert len(posts) == 3
    top = next(p for p in posts if p["video_id"] == _SHARED_VIDEO_ID)
    assert top["plays"] == 1817

    # 4. Overview aggregate
    r_overview = client.get(
        f"/media/channels/overview?account_id={acc_id}",
        headers=_auth(tokens["analyst"]),
    )
    assert r_overview.status_code == 200
    overview = r_overview.json()
    assert overview["posts"] == 3

    # 5. Re-upload is additive upsert (same video_id -> update, not duplicate)
    r_reupload = client.post(
        "/media/channels/upload",
        data={"account_id": acc_id},
        files={"file": ("channels_video_data.json", file_bytes, "application/json")},
        headers=_auth(tokens["admin"]),
    )
    assert r_reupload.status_code == 200
    r_posts2 = client.get(
        f"/media/channels/posts?account_id={acc_id}",
        headers=_auth(tokens["analyst"]),
    )
    assert len(r_posts2.json()) == 3


def test_channels_api_upload_csv_still_works(client, tokens):
    """The manual-upload CSV path (what a human downloads by hand) must
    keep working alongside the collector's JSON path."""
    r_create = client.post(
        "/media/channels/accounts",
        json={"name": "视频号CSV测试账号"},
        headers=_auth(tokens["admin"]),
    )
    assert r_create.status_code == 201
    acc_id = r_create.json()["id"]

    csv_text = _make_channels_csv_df().to_csv(header=False, index=False)
    file_bytes = ("﻿" + csv_text).encode("utf-8")

    r_upload = client.post(
        "/media/channels/upload",
        data={"account_id": acc_id},
        files={"file": ("channels_example.csv", file_bytes, "text/csv")},
        headers=_auth(tokens["admin"]),
    )
    assert r_upload.status_code == 200
    assert r_upload.json()["total"] == 1

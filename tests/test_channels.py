"""Unit and integration tests for WeChat Channels (视频号) ETL and API endpoints.

Verified live against a real export from a 视频号 account (see
app/db/etl/channels.py's module docstring; sample data below is synthetic,
not the real export). Two independent input formats are tested — the JSON
API (collector's primary path, confirmed to return full account history in
one call) and the CSV a human gets from manually clicking "下载表格" (capped
to whatever date-range filter was active — see that module for why the
collector doesn't use this path anymore).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.db.etl.channels import (
    _float_or_none,
    _int_or_none,
    parse_channels_api_json,
    parse_channels_xlsx,
)
from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401

SAMPLE_CSV_PATH = Path(__file__).parent.parent / "data" / "channels_example.csv"
SAMPLE_JSON_PATH = Path(__file__).parent.parent / "data" / "channels_example.json"

# Sample files are git-ignored (see .gitignore's `data/` rule — no real
# exports checked in). Same convention as tests/test_xhs.py /
# tests/test_zhihu.py's "real example file" smoke tests: skip rather than
# fail when they're not present locally.
_MISSING_SAMPLE = "sample file not present (data/ is git-ignored; drop a synthetic-content copy there locally to run this test)"


# ── CSV path ─────────────────────────────────────────────────────────────

def test_parse_channels_xlsx_with_sample_file():
    if not SAMPLE_CSV_PATH.exists():
        pytest.skip(_MISSING_SAMPLE)
    df_raw = pd.read_csv(SAMPLE_CSV_PATH, header=None, dtype=str)

    rows = parse_channels_xlsx(df_raw, account_id=1)

    assert len(rows) == 1, f"Expected 1 parsed row from sample file, got {len(rows)}"

    row0 = rows[0]
    assert row0["video_id"] == "export/UzFfBgAAxOOgWDk7RwySjMzT4DCaK42Y3QBKOFW-O1qJ9TXaDA"
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

def test_parse_channels_api_json_with_sample_file():
    if not SAMPLE_JSON_PATH.exists():
        pytest.skip(_MISSING_SAMPLE)
    raw = SAMPLE_JSON_PATH.read_bytes()

    rows = parse_channels_api_json(raw, account_id=1)

    assert len(rows) == 3, f"Expected 3 parsed rows from sample file, got {len(rows)}"

    row0 = rows[0]
    assert row0["video_id"] == "export/UzFfBgAAxOOgWDk7RwySjMzT4DCaK42Y3QBKOFW-O1qJ9TXaDA"
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
    if not (SAMPLE_CSV_PATH.exists() and SAMPLE_JSON_PATH.exists()):
        pytest.skip(_MISSING_SAMPLE)
    csv_rows = parse_channels_xlsx(
        pd.read_csv(SAMPLE_CSV_PATH, header=None, dtype=str), account_id=1
    )
    json_rows = parse_channels_api_json(SAMPLE_JSON_PATH.read_bytes(), account_id=1)

    csv_row = next(r for r in csv_rows if r["video_id"].endswith("O1qJ9TXaDA"))
    json_row = next(r for r in json_rows if r["video_id"].endswith("O1qJ9TXaDA"))

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
    if not SAMPLE_JSON_PATH.exists():
        pytest.skip(_MISSING_SAMPLE)
    # 1. Create a WeChat Channels account
    r_create = client.post(
        "/media/channels/accounts",
        json={"name": "视频号测试账号"},
        headers=_auth(tokens["admin"]),
    )
    assert r_create.status_code == 201
    acc_id = r_create.json()["id"]

    # 2. Upload the sample JSON (the collector's actual output format)
    with open(SAMPLE_JSON_PATH, "rb") as f:
        file_bytes = f.read()

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
    top = next(p for p in posts if p["video_id"].endswith("O1qJ9TXaDA"))
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
    if not SAMPLE_CSV_PATH.exists():
        pytest.skip(_MISSING_SAMPLE)
    r_create = client.post(
        "/media/channels/accounts",
        json={"name": "视频号CSV测试账号"},
        headers=_auth(tokens["admin"]),
    )
    assert r_create.status_code == 201
    acc_id = r_create.json()["id"]

    with open(SAMPLE_CSV_PATH, "rb") as f:
        file_bytes = f.read()

    r_upload = client.post(
        "/media/channels/upload",
        data={"account_id": acc_id},
        files={"file": ("channels_example.csv", file_bytes, "text/csv")},
        headers=_auth(tokens["admin"]),
    )
    assert r_upload.status_code == 200
    assert r_upload.json()["total"] == 1

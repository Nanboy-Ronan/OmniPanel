"""Unit and integration tests for Pugongying (蒲公英) ETL, API endpoints, and collector runner."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from app.db.etl.pgy import parse_pgy_xlsx
from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401

# app/db/etl/pgy.py reads columns by fixed position (0-103), skipping the
# first 3 rows (merged header groups). This mirrors that exact layout with
# synthetic values so no real export file needs to be tracked in the repo.
_N_COLS = 104


def _blank_header_rows(n: int = 3) -> list[list[str]]:
    return [[""] * _N_COLS for _ in range(n)]


def _one_row(overrides: dict | None = None) -> list:
    """Build one synthetic data row by column index (see app/db/etl/pgy.py)."""
    row = [""] * _N_COLS
    defaults = {
        0: "2026/01/15",                       # data_date
        1: "示例博主",                          # blogger_nickname
        2: "https://example.com/blogger/1",     # blogger_url
        3: 50000,                              # blogger_fans
        4: "优秀",                              # blogger_health
        5: "示例商品测评｜日常分享",              # note_title
        6: "https://example.com/note/1",        # note_url
        7: "视频",                              # note_type
        8: "2026/01/10",                       # publish_date
        9: "蒲公英",                            # note_source
        10: "note_0000000001",                 # note_id
        11: "测评",                             # content_tag
        12: "order_0001",                      # order_id
        13: "示例品牌合作视频",                  # cooperation_name
        14: "示例品牌",                          # report_brand
        15: "示例投放账号",                      # order_account
        16: 1000.0,                            # blogger_quote
        17: 100.0,                             # service_fee
        18: "否",                              # is_premium_mode
        19: "示例商品",                          # spu_name
        20: 10000,                             # impressions
        21: 1200,                              # reads
        22: 1100,                              # read_uv
        23: "60.00%",                          # play_rate_5s
        24: "50.00%",                          # read_rate_3s
        25: 30.0,                              # video_duration
        26: 20.0,                              # avg_view_duration
        27: "70.00%",                          # video_completion_rate
        28: 100,                               # interactions
        29: "8.00%",                           # interaction_rate
        30: 80,                                # likes
        31: 10,                                # collects
        32: 5,                                 # comments
        33: 5,                                 # shares
        34: 3,                                 # follows
        35: 8000,                              # organic_impressions
        36: 900,                               # organic_reads
        37: 1500,                              # paid_impressions
        38: 200,                               # paid_reads
        39: 500,                               # boosted_impressions
        40: 100,                               # boosted_reads
        53: 0.9,                               # cost_per_read
        54: 9.5,                               # cost_per_interaction
        55: "文字组件", 56: "示例文案", 57: 5000, 58: 100, 59: 90, 60: "2.00%",
        61: "底部条", 62: "示例底部文案", 63: 4000, 64: 80, 65: 70, 66: "2.00%",
        67: "互动组件", 68: "示例互动标题", 69: 3000, 70: 50, 71: "1.67%",
        72: "评论区", 73: "示例评论文案", 74: 2000, 75: 30, 76: 25, 77: "1.50%",
        78: "25.00%", 79: "20.00%", 80: "80.00%",   # fan_ratio, female_ratio, male_ratio
        81: "5.00%", 82: "20.00%", 83: "40.00%", 84: "25.00%", 85: "10.00%",  # age
        86: "iPhone", 87: "40.00%", 88: "Android", 89: "35.00%", 90: "其他", 91: "25.00%",
        92: "广东", 93: "15.00%", 94: "江苏", 95: "12.00%", 96: "浙江", 97: "10.00%",
        98: "美妆", 99: "30.00%", 100: "健康", 101: "20.00%", 102: "母婴", 103: "15.00%",
    }
    if overrides:
        defaults.update(overrides)
    for idx, val in defaults.items():
        row[idx] = val
    return row


def _make_pgy_xlsx_bytes(rows: list[list] | None = None) -> bytes:
    if rows is None:
        rows = [_one_row()]
    all_rows = _blank_header_rows() + rows
    df = pd.DataFrame(all_rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    return buf.getvalue()


def test_parse_pgy_xlsx_with_sample_file():
    file_bytes = _make_pgy_xlsx_bytes([_one_row(), _one_row({10: "note_0000000002"})])
    df_raw = pd.read_excel(io.BytesIO(file_bytes), header=None)

    rows = parse_pgy_xlsx(df_raw)

    assert len(rows) == 2, f"Expected 2 parsed rows, got {len(rows)}"

    row0 = rows[0]
    assert row0["blogger_nickname"] == "示例博主"
    assert row0["note_title"] == "示例商品测评｜日常分享"
    assert row0["note_type"] == "视频"
    assert row0["note_id"] == "note_0000000001"
    assert row0["cooperation_name"] == "示例品牌合作视频"
    assert row0["blogger_quote"] == 1000.0
    assert row0["service_fee"] == 100.0
    assert row0["impressions"] == 10000
    assert row0["reads"] == 1200
    assert row0["read_uv"] == 1100
    assert row0["interactions"] == 100
    assert row0["likes"] == 80
    assert row0["collects"] == 10
    assert row0["comments"] == 5
    assert row0["shares"] == 5
    assert row0["follows"] == 3
    assert row0["cost_per_read"] == 0.9
    assert row0["cost_per_interaction"] == 9.5
    assert row0["fan_ratio"] == "25.00%"
    assert row0["female_ratio"] == "20.00%"
    assert row0["male_ratio"] == "80.00%"

    # Check JSON string serialization
    assert isinstance(row0["audience_json"], str)
    assert isinstance(row0["component_json"], str)


def test_parse_pgy_xlsx_empty():
    df_empty = pd.DataFrame()
    rows = parse_pgy_xlsx(df_empty)
    assert rows == []


def test_pgy_api_upload_and_query(client, tokens):
    # 1. Create an XHS account first
    r_create = client.post(
        "/media/xhs/accounts",
        json={"name": "蒲公英测试账号", "account_type": "company"},
        headers=_auth(tokens["admin"]),
    )
    assert r_create.status_code == 201
    acc_id = r_create.json()["id"]

    # 2. Upload a synthetic pgy export
    file_bytes = _make_pgy_xlsx_bytes([_one_row(), _one_row({10: "note_0000000002"})])

    r_upload = client.post(
        "/media/pgy/upload",
        data={"account_id": acc_id},
        files={"file": ("pgy_example.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=_auth(tokens["admin"]),
    )
    assert r_upload.status_code == 200
    upload_res = r_upload.json()
    assert upload_res["total"] == 2
    assert upload_res["upserted"] == 2

    # 3. Query notes
    r_notes = client.get(
        f"/media/pgy/notes?account_id={acc_id}",
        headers=_auth(tokens["analyst"]),
    )
    assert r_notes.status_code == 200
    notes = r_notes.json()
    assert len(notes) == 2

    # 4. Query bloggers aggregate
    r_bloggers = client.get(
        f"/media/pgy/bloggers?account_id={acc_id}",
        headers=_auth(tokens["analyst"]),
    )
    assert r_bloggers.status_code == 200
    bloggers = r_bloggers.json()
    assert len(bloggers) > 0

    # 5. Query campaigns aggregate
    r_campaigns = client.get(
        f"/media/pgy/campaigns?account_id={acc_id}",
        headers=_auth(tokens["analyst"]),
    )
    assert r_campaigns.status_code == 200
    campaigns = r_campaigns.json()
    assert len(campaigns) > 0

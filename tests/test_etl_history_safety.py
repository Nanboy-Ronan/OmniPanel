"""Media-export imports must never erase history.

* A renamed/removed export column fails the import with a message listing
  the missing columns (instead of writing NULL for that metric on every row).
* An upsert whose incoming value is NULL keeps the stored value.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.etl.pgy import parse_pgy_xlsx, upsert_pgy_notes
from app.db.etl.xhs import parse_xhs_xlsx, upsert_xhs_posts
from app.db.etl.zhihu import parse_zhihu_csv, upsert_zhihu_posts
from app.db.etl.channels import upsert_channels_posts
from tests.test_api_endpoints import client, tokens  # noqa: F401



def _pgy_sample() -> pd.DataFrame:
    """Synthetic 蒲公英 export in the verified layout (see tests/test_pgy.py)."""
    import io

    from tests.test_pgy import _make_pgy_xlsx_bytes

    return pd.read_excel(io.BytesIO(_make_pgy_xlsx_bytes()), header=None, dtype=str)

_XHS_HEADERS = [
    "笔记标题", "首次发布时间", "体裁", "曝光", "观看量", "封面点击率",
    "点赞", "评论", "收藏", "涨粉", "分享", "人均观看时长", "弹幕",
]


def _xhs_raw(headers: list[str], row: dict) -> pd.DataFrame:
    banner = {h: "banner" for h in headers}
    header = {h: h for h in headers}
    return pd.DataFrame([banner, header, row], columns=headers)


# ── header validation ───────────────────────────────────────────────────────

def test_xhs_renamed_column_fails_with_missing_list():
    headers = [("阅读量" if h == "观看量" else h) for h in _XHS_HEADERS if h != "弹幕"]
    row = {h: "1" for h in headers} | {"笔记标题": "t", "首次发布时间": "2026年06月01日"}
    with pytest.raises(ValueError) as exc:
        parse_xhs_xlsx(_xhs_raw(headers, row))
    assert "观看量" in str(exc.value) and "弹幕" in str(exc.value)


def test_zhihu_missing_column_fails_with_missing_list():
    df = pd.DataFrame([{"标题": "t", "发布时间": "2026-05-31", "链接": "u", "阅读": "1"}])
    with pytest.raises(ValueError) as exc:
        parse_zhihu_csv(df, "article")
    for col in ("点赞", "喜欢", "评论", "收藏", "分享"):
        assert col in str(exc.value)


def test_zhihu_qa_requires_play_column():
    cols = ["标题", "发布时间", "链接", "阅读", "点赞", "喜欢", "评论", "收藏", "分享"]
    df = pd.DataFrame([{c: "1" for c in cols} | {"发布时间": "2026-05-31"}])
    with pytest.raises(ValueError, match="播放"):
        parse_zhihu_csv(df, "qa")


def test_pgy_shifted_column_fails_instead_of_misaligning_metrics():
    df = _pgy_sample()
    assert parse_pgy_xlsx(df)  # the verified layout still parses
    shifted = df.copy()
    shifted.iloc[2, 21] = "阅读次数"  # 阅读量 renamed by the platform
    with pytest.raises(ValueError, match="阅读量"):
        parse_pgy_xlsx(shifted)
    with pytest.raises(ValueError, match="列"):
        parse_pgy_xlsx(df.iloc[:, :60])


def test_xhs_upload_endpoint_reports_missing_columns(client, tokens):
    import io

    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    acc = client.post("/media/xhs/accounts", json={"name": "列校验", "account_type": "company"},
                      headers=headers).json()["id"]
    cols = [h for h in _XHS_HEADERS if h != "曝光"]
    df = _xhs_raw(cols, {h: "1" for h in cols} | {"笔记标题": "t", "首次发布时间": "2026年06月01日"})
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    r = client.post("/media/xhs/upload", data={"account_id": acc},
                    files={"file": ("x.xlsx", buf.getvalue())}, headers=headers)
    assert r.status_code == 400
    assert "曝光" in r.json()["detail"]


# ── COALESCE upserts ────────────────────────────────────────────────────────

@pytest.fixture
def session(pg_sync_url):
    engine = create_engine(pg_sync_url, future=True)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _xhs_account(session) -> int:
    from app.db.models import XhsAccount
    acc = XhsAccount(name="history-safety", pgy_enabled=True)
    session.add(acc)
    session.commit()
    return acc.id


def test_xhs_null_metric_does_not_erase_history(session):
    from app.db.models import XhsPost
    acc = _xhs_account(session)
    base = {"title": "t", "publish_date": date(2026, 6, 1), "genre": "图文", "impressions": 500,
            "views": 200, "cover_click_rate": 0.1, "likes": 3, "comments": 1, "collects": 1,
            "new_followers": 1, "shares": 1, "avg_watch_time": 2.0, "danmu": 0}
    upsert_xhs_posts([base], acc, session)
    upsert_xhs_posts([base | {"views": None, "likes": 9}], acc, session)
    post = session.execute(select(XhsPost)).scalar_one()
    session.refresh(post)
    assert post.views == 200   # NULL kept history
    assert post.likes == 9     # real values still update


def test_zhihu_null_metric_does_not_erase_history(session):
    from app.db.models import ZhihuPost
    row = {"content_type": "article", "title": "t", "publish_date": date(2026, 5, 31), "url": "u",
           "reads": 100, "plays": None, "likes": 1, "favorites": 1, "comments": 1, "collects": 1, "shares": 1}
    upsert_zhihu_posts([row], session)
    upsert_zhihu_posts([row | {"reads": None, "url": None, "likes": 5}], session)
    post = session.execute(select(ZhihuPost)).scalar_one()
    session.refresh(post)
    assert (post.reads, post.url, post.likes) == (100, "u", 5)


def test_pgy_null_metric_does_not_erase_history(session):
    from app.db.models import PgyNote
    acc = _xhs_account(session)
    rows = parse_pgy_xlsx(_pgy_sample())[:1]
    upsert_pgy_notes(rows, acc, session)
    stored = session.execute(select(PgyNote)).scalar_one()
    original_reads = stored.reads
    assert original_reads is not None
    upsert_pgy_notes([rows[0] | {"reads": None}], acc, session)
    session.refresh(stored)
    assert stored.reads == original_reads


def test_channels_json_upload_keeps_csv_only_fields(session):
    from app.db.models import WxChannelsAccount, WxChannelsPost
    acc = WxChannelsAccount(name="ch-history")
    session.add(acc)
    session.commit()
    csv_row = {"video_id": "v1", "title": "t", "publish_date": date(2026, 8, 1), "plays": 10,
               "wecom_link_clicks": 7, "raw_payload": {}}
    upsert_channels_posts([csv_row], acc.id, session)
    upsert_channels_posts([csv_row | {"plays": 20, "wecom_link_clicks": None}], acc.id, session)
    post = session.execute(select(WxChannelsPost)).scalar_one()
    session.refresh(post)
    assert (post.plays, post.wecom_link_clicks) == (20, 7)

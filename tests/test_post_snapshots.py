"""Post metric snapshots (append-only history) and the raw upload archive."""
import datetime as dt
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import upload_and_poll
from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401
from tests.test_xhs import _make_xhs_xlsx_bytes, _one_row

DAY1, DAY2 = dt.date(2026, 10, 14), dt.date(2026, 10, 15)


@pytest.fixture
def xhs_account(client, tokens):
    r = client.post("/media/xhs/accounts", json={"name": "snapshot-account"}, headers=_auth(tokens["admin"]))
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture
def day(monkeypatch):
    import app.db.etl._upsert as upsert

    current = {"day": DAY1}
    monkeypatch.setattr(upsert, "capture_day", lambda: current["day"])
    return current


def _upload_xhs(client, tokens, account_id, rows):
    data = _make_xhs_xlsx_bytes(rows)
    r = client.post("/media/xhs/upload", data={"account_id": account_id},
                    files={"file": ("xhs.xlsx", data, "application/octet-stream")},
                    headers=_auth(tokens["analyst"]))
    assert r.status_code == 200, r.text
    return data, r.json()


def _rows(model):
    import app.db as db
    with db.SyncSessionLocal() as s:
        return s.execute(select(model)).scalars().all()


def test_xhs_upload_keeps_one_snapshot_per_day(client, tokens, xhs_account, day):
    from app.db.models import XhsPost, XhsPostSnapshot

    _upload_xhs(client, tokens, xhs_account, [_one_row(**{"观看量": "100"})])
    _upload_xhs(client, tokens, xhs_account, [_one_row(**{"观看量": "150"})])  # same day replaces
    day["day"] = DAY2
    _, body = _upload_xhs(client, tokens, xhs_account, [_one_row(**{"观看量": "300"})])
    assert body["snapshots"] == 1

    snaps = sorted(_rows(XhsPostSnapshot), key=lambda s: s.captured_on)
    assert [(s.captured_on, s.views) for s in snaps] == [(DAY1, 150), (DAY2, 300)]
    (post,) = _rows(XhsPost)
    assert {s.post_id for s in snaps} == {post.id}
    assert post.views == 300


def test_snapshot_records_what_the_file_said(client, tokens, xhs_account, day):
    """The post row keeps the old value for a blank metric; the snapshot does not."""
    from app.db.models import XhsPost, XhsPostSnapshot

    _upload_xhs(client, tokens, xhs_account, [_one_row(**{"点赞": "30"})])
    day["day"] = DAY2
    _upload_xhs(client, tokens, xhs_account, [_one_row(**{"点赞": "-"})])
    (post,) = _rows(XhsPost)
    assert post.likes == 30
    latest = max(_rows(XhsPostSnapshot), key=lambda s: s.captured_on)
    assert latest.likes is None


def test_upload_is_archived_and_linked(client, tokens, xhs_account, day):
    from app.db.models import RawUploadFile, XhsPostSnapshot

    data, _ = _upload_xhs(client, tokens, xhs_account, [_one_row()])
    (raw,) = _rows(RawUploadFile)
    assert raw.source == "xhs_posts" and raw.account_id == xhs_account
    assert raw.sha256 == hashlib.sha256(data).hexdigest() and raw.size_bytes == len(data)
    assert Path(raw.archive_path).read_bytes() == data
    (snap,) = _rows(XhsPostSnapshot)
    assert snap.source_file_id == raw.id


def test_failed_upload_leaves_no_archive(client, tokens, xhs_account):
    from app.config import settings
    from app.db.models import RawUploadFile

    r = client.post("/media/xhs/upload", data={"account_id": xhs_account},
                    files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes([]), "application/octet-stream")},
                    headers=_auth(tokens["analyst"]))
    assert r.status_code == 400
    assert _rows(RawUploadFile) == []
    archive = Path(settings.raw_archive_dir)
    assert not archive.exists() or not any(p.is_file() for p in archive.rglob("*"))


def test_other_post_tables_snapshot(client, tokens, day):
    import app.db as db
    from app.db.etl.channels import upsert_channels_posts
    from app.db.etl.pgy import upsert_pgy_notes
    from app.db.etl.zhihu import upsert_zhihu_posts
    from app.db.models import (PgyNoteSnapshot, WxChannelsAccount, WxChannelsPostSnapshot, XhsAccount,
                               ZhihuPostSnapshot)

    with db.SyncSessionLocal() as s:
        xhs, channels = XhsAccount(name="pgy-snap"), WxChannelsAccount(name="ch-snap")
        s.add_all([xhs, channels])
        s.commit()
        assert upsert_zhihu_posts([{"content_type": "article", "title": "z", "publish_date": DAY1,
                                    "reads": 5}], s)["snapshots"] == 1
        assert upsert_channels_posts([{"video_id": "v1", "title": "c", "plays": 9}], channels.id, s,
                                     captured_on=DAY2)["snapshots"] == 1
        assert upsert_pgy_notes([{"note_id": "n1", "reads": 7, "cost_per_interaction": 1.5}], xhs.id,
                                s)["snapshots"] == 1
    assert [x.reads for x in _rows(ZhihuPostSnapshot)] == [5]
    assert [(x.captured_on, x.plays) for x in _rows(WxChannelsPostSnapshot)] == [(DAY2, 9)]
    assert [(x.reads, x.cost_per_interaction) for x in _rows(PgyNoteSnapshot)] == [(7, 1.5)]


def test_order_upload_is_archived_with_batch(client, tokens):
    from app.db.models import RawUploadFile

    csv = ("订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
           "ar1,2026-03-10 10:00:00,13900000001,item,1,10\n").encode()
    batch = upload_and_poll(client, _auth(tokens["admin"]), csv, "orders.csv")
    (raw,) = _rows(RawUploadFile)
    assert raw.source == "orders" and raw.upload_batch_id == batch["id"]
    assert Path(raw.archive_path).read_bytes() == csv


def test_retention_removes_old_files_but_keeps_rows(client, tokens, xhs_account, day):
    import app.db as db
    from app.db.models import RawUploadFile
    from app.db.raw_archive import purge_expired

    _upload_xhs(client, tokens, xhs_account, [_one_row()])
    (raw,) = _rows(RawUploadFile)
    path = Path(raw.archive_path)
    with db.SyncSessionLocal() as s:
        assert purge_expired(s) == 0
        assert purge_expired(s, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=800)) == 1
    assert not path.exists()
    (raw,) = _rows(RawUploadFile)
    assert raw.archive_path is None and raw.purged_at is not None


def test_real_sample_exports_snapshot_every_row(client, tokens, day):
    """Snapshots join on the parsed key; a type mismatch would silently match nothing."""
    import json

    import app.db as db
    from app.db.etl.channels import parse_channels_api_json, parse_channels_xlsx, upsert_channels_posts
    from app.db.etl.pgy import parse_pgy_xlsx, upsert_pgy_notes
    from app.db.etl.zhihu import parse_zhihu_csv, upsert_zhihu_posts
    from app.db.models import WxChannelsAccount, XhsAccount
    from app.utils.spreadsheet import read_excel_safely
    from app.views.media.zhihu import _read_zhihu_file
    import pandas as pd

    data = Path(__file__).parent.parent / "data"
    with db.SyncSessionLocal() as s:
        xhs, channels = XhsAccount(name="pgy-real"), WxChannelsAccount(name="ch-real")
        s.add_all([xhs, channels])
        s.commit()
        # Sample exports are kept out of git (they may be real data); use them when present.
        results = []
        if (data / "pgy_example.xlsx").exists():
            results.append(upsert_pgy_notes(parse_pgy_xlsx(read_excel_safely(
                str(data / "pgy_example.xlsx"), header=None, dtype=str)), xhs.id, s))
        if (data / "channels_example.csv").exists():
            results.append(upsert_channels_posts(parse_channels_xlsx(pd.read_csv(
                data / "channels_example.csv", header=None, dtype=str), channels.id), channels.id, s))
        if (data / "channels_example.json").exists():
            results.append(upsert_channels_posts(parse_channels_api_json(
                (data / "channels_example.json").read_bytes(), channels.id), channels.id, s))
        for name, kind in (("zhihu_article.xls", "article"), ("zhihu_qa.xls", "qa")):
            if (data / name).exists():
                results.append(upsert_zhihu_posts(parse_zhihu_csv(_read_zhihu_file(str(data / name)), kind), s))
    if not results:
        pytest.skip("no local sample exports in data/")
    for result in results:
        assert result["total"] > 0
        assert result["snapshots"] == result["total"]


def test_unwritable_archive_does_not_block_import(client, tokens, xhs_account, day, tmp_path, monkeypatch):
    from app.config import settings
    from app.db.models import RawUploadFile, XhsPostSnapshot

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    monkeypatch.setattr(settings, "raw_archive_dir", str(blocked / "archive"))
    try:
        _upload_xhs(client, tokens, xhs_account, [_one_row()])
    finally:
        blocked.chmod(0o700)
    (raw,) = _rows(RawUploadFile)
    assert raw.archive_path is None and raw.sha256
    assert [s.source_file_id for s in _rows(XhsPostSnapshot)] == [raw.id]

"""Tests for Xiaohongshu (小红书) xlsx upload and post API.

Structure
─────────
Part 1 — ETL unit tests (no database, no HTTP)
  TestParseXhsXlsx       — parse_xhs_xlsx() correctness
  TestParseXhsEdgeCases  — malformed / partial input
  TestInvalidReason      — negative-price guard via _invalid_reason()

Part 2 — API integration tests (real test DB via pg_async_url)
  TestXhsUploadEndpoint  — POST /media/xhs/upload
  TestXhsPostsEndpoint   — GET  /media/xhs/posts
  TestXhsAuth            — 401 / role checks

These tests follow the project TDD convention: specs are documented before
(or alongside) the implementation so they can catch regressions.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest

# ── Part 1 — ETL unit tests (no DB) ───────────────────────────────────────────

from app.db.etl.xhs import parse_xhs_xlsx, _parse_date, _int_or_none, _float_or_none


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_raw_df(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame that mimics the XHS xlsx layout:
    row 0 = banner, row 1 = headers, row 2+ = data.
    """
    headers = [
        "笔记标题", "首次发布时间", "体裁",
        "曝光", "观看量", "封面点击率",
        "点赞", "评论", "收藏", "涨粉", "分享", "人均观看时长", "弹幕",
    ]
    banner = {h: "最多导出排序后前1000条笔记" for h in headers}
    data = [banner, {h: h for h in headers}] + rows  # row0=banner, row1=real headers
    df = pd.DataFrame(data, columns=headers)
    return df


def _one_row(**overrides) -> dict:
    base = {
        "笔记标题": "测试笔记标题",
        "首次发布时间": "2026年06月01日12时00分00秒",
        "体裁": "图文",
        "曝光": "500",
        "观看量": "200",
        "封面点击率": "0.05",
        "点赞": "30",
        "评论": "5",
        "收藏": "10",
        "涨粉": "3",
        "分享": "2",
        "人均观看时长": "45.0",
        "弹幕": "0",
    }
    base.update(overrides)
    return base


# ─── TestParseXhsXlsx ─────────────────────────────────────────────────────────

class TestParseXhsXlsx:
    """parse_xhs_xlsx correctly maps all 13 XHS columns to model fields."""

    def test_returns_list_of_dicts(self):
        df = _make_raw_df([_one_row()])
        result = parse_xhs_xlsx(df)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_title_is_preserved(self):
        df = _make_raw_df([_one_row(**{"笔记标题": "我的笔记"})])
        assert parse_xhs_xlsx(df)[0]["title"] == "我的笔记"

    def test_publish_date_parsed_correctly(self):
        df = _make_raw_df([_one_row(**{"首次发布时间": "2025年12月31日08时30分00秒"})])
        assert parse_xhs_xlsx(df)[0]["publish_date"] == date(2025, 12, 31)

    def test_genre_mapped(self):
        df = _make_raw_df([_one_row(**{"体裁": "视频"})])
        assert parse_xhs_xlsx(df)[0]["genre"] == "视频"

    def test_numeric_fields_are_integers(self):
        df = _make_raw_df([_one_row()])
        row = parse_xhs_xlsx(df)[0]
        for field in ("impressions", "views", "likes", "comments",
                      "collects", "new_followers", "shares", "danmu"):
            assert isinstance(row[field], (int, type(None))), \
                f"{field} should be int, got {type(row[field])}"

    def test_float_fields_are_floats(self):
        df = _make_raw_df([_one_row()])
        row = parse_xhs_xlsx(df)[0]
        for field in ("cover_click_rate", "avg_watch_time"):
            assert isinstance(row[field], (float, type(None))), \
                f"{field} should be float, got {type(row[field])}"

    def test_impressions_value(self):
        df = _make_raw_df([_one_row(**{"曝光": "1234"})])
        assert parse_xhs_xlsx(df)[0]["impressions"] == 1234

    def test_cover_click_rate_value(self):
        df = _make_raw_df([_one_row(**{"封面点击率": "0.123"})])
        assert abs(parse_xhs_xlsx(df)[0]["cover_click_rate"] - 0.123) < 1e-9

    def test_multiple_rows_all_parsed(self):
        rows = [_one_row(**{"笔记标题": f"笔记{i}", "首次发布时间": f"202{i}年01月0{i+1}日00时00分00秒"})
                for i in range(1, 5)]
        df = _make_raw_df(rows)
        result = parse_xhs_xlsx(df)
        assert len(result) == 4

    def test_required_keys_present(self):
        df = _make_raw_df([_one_row()])
        row = parse_xhs_xlsx(df)[0]
        required = {
            "title", "publish_date", "genre",
            "impressions", "views", "cover_click_rate",
            "likes", "comments", "collects", "new_followers",
            "shares", "avg_watch_time", "danmu",
        }
        assert required.issubset(row.keys())

    def test_zero_values_kept_not_nulled(self):
        df = _make_raw_df([_one_row(**{"曝光": "0", "点赞": "0"})])
        row = parse_xhs_xlsx(df)[0]
        assert row["impressions"] == 0
        assert row["likes"] == 0

    def test_parses_real_example_file(self):
        """Smoke test against the actual example file in data/."""
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "data", "xiaohongshu.xlsx")
        if not os.path.exists(path):
            pytest.skip("example file not present")
        df_raw = pd.read_excel(path, header=None, dtype=str)
        rows = parse_xhs_xlsx(df_raw)
        assert len(rows) == 131
        assert rows[0]["title"] == "搞懂咖啡因 & 茶碱 喝奶茶比喝咖啡还失眠？"
        assert rows[0]["publish_date"] == date(2026, 6, 2)
        assert rows[0]["genre"] == "图文"


# ─── TestParseXhsEdgeCases ────────────────────────────────────────────────────

class TestParseXhsEdgeCases:
    """parse_xhs_xlsx handles malformed / partial input gracefully."""

    def test_empty_title_row_is_skipped(self):
        df = _make_raw_df([_one_row(**{"笔记标题": ""})])
        assert parse_xhs_xlsx(df) == []

    def test_whitespace_only_title_is_skipped(self):
        df = _make_raw_df([_one_row(**{"笔记标题": "   "})])
        assert parse_xhs_xlsx(df) == []

    def test_unparseable_date_row_is_skipped(self):
        df = _make_raw_df([_one_row(**{"首次发布时间": "not-a-date"})])
        assert parse_xhs_xlsx(df) == []

    def test_mixed_valid_and_invalid_rows(self):
        rows = [
            _one_row(**{"笔记标题": "好笔记"}),         # valid
            _one_row(**{"笔记标题": ""}),               # no title → skip
            _one_row(**{"首次发布时间": "invalid"}),     # bad date → skip
            _one_row(**{"笔记标题": "另一篇", "首次发布时间": "2026年05月01日00时00分00秒"}),  # valid
        ]
        df = _make_raw_df(rows)
        result = parse_xhs_xlsx(df)
        assert len(result) == 2
        assert result[0]["title"] == "好笔记"
        assert result[1]["title"] == "另一篇"

    def test_non_numeric_metric_becomes_none(self):
        df = _make_raw_df([_one_row(**{"曝光": "N/A", "点赞": "--"})])
        row = parse_xhs_xlsx(df)[0]
        assert row["impressions"] is None
        assert row["likes"] is None

    def test_empty_genre_becomes_none(self):
        df = _make_raw_df([_one_row(**{"体裁": ""})])
        assert parse_xhs_xlsx(df)[0]["genre"] is None

    def test_no_data_rows_returns_empty_list(self):
        # Only banner + header rows, no actual data
        headers = ["笔记标题", "首次发布时间", "体裁",
                   "曝光", "观看量", "封面点击率",
                   "点赞", "评论", "收藏", "涨粉", "分享", "人均观看时长", "弹幕"]
        df = pd.DataFrame(
            [
                {h: "最多导出排序后前1000条笔记" for h in headers},
                {h: h for h in headers},
            ],
            columns=headers,
        )
        assert parse_xhs_xlsx(df) == []


# ─── TestHelperFunctions ──────────────────────────────────────────────────────

class TestHelperFunctions:
    """Internal helpers behave correctly for edge inputs."""

    def test_parse_date_standard_format(self):
        assert _parse_date("2026年06月02日19时05分59秒") == date(2026, 6, 2)

    def test_parse_date_single_digit_month(self):
        assert _parse_date("2025年1月5日00时00分00秒") == date(2025, 1, 5)

    def test_parse_date_none_input(self):
        assert _parse_date(None) is None

    def test_parse_date_invalid_returns_none(self):
        assert _parse_date("not a date") is None

    def test_int_or_none_valid(self):
        assert _int_or_none("42") == 42
        assert _int_or_none("0") == 0

    def test_int_or_none_float_string(self):
        assert _int_or_none("3.0") == 3

    def test_int_or_none_invalid(self):
        assert _int_or_none("N/A") is None
        assert _int_or_none(None) is None

    def test_float_or_none_valid(self):
        assert abs(_float_or_none("0.037") - 0.037) < 1e-9

    def test_float_or_none_invalid(self):
        assert _float_or_none("--") is None


# ─── TestNegativePriceRejection ───────────────────────────────────────────────

class TestNegativePriceRejection:
    """Negative-price rows are classified as invalid by _invalid_reason()."""

    def test_negative_price_returns_reason_string(self):
        from app.db.etl.normalize import _invalid_reason
        row = {
            "order_id": "X001",
            "order_date": date(2026, 1, 1),
            "customer_key": "13800000000",
            "price": -10.0,
        }
        reason = _invalid_reason(row)
        assert reason is not None
        assert "Negative" in reason or "negative" in reason or "-10" in reason

    def test_zero_price_is_valid(self):
        from app.db.etl.normalize import _invalid_reason
        row = {
            "order_id": "X002",
            "order_date": date(2026, 1, 1),
            "customer_key": "13800000000",
            "price": 0.0,
        }
        assert _invalid_reason(row) is None

    def test_positive_price_is_valid(self):
        from app.db.etl.normalize import _invalid_reason
        row = {
            "order_id": "X003",
            "order_date": date(2026, 1, 1),
            "customer_key": "13800000000",
            "price": 99.0,
        }
        assert _invalid_reason(row) is None


# ── Part 2 — API integration tests ────────────────────────────────────────────

from test_api_endpoints import client, tokens  # noqa: F401


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def account(client, tokens):
    """Return the id of a shared 'test-account' XHS account (create if absent)."""
    r = client.post(
        "/media/xhs/accounts",
        json={"name": "test-account"},
        headers=_auth(tokens["admin"]),
    )
    if r.status_code == 201:
        return r.json()["id"]
    # 409 means it already exists (duplicate name) — look it up
    accs = client.get("/media/xhs/accounts", headers=_auth(tokens["admin"])).json()
    return next(a["id"] for a in accs if a["name"] == "test-account")


def _make_xhs_xlsx_bytes(rows: list[dict] | None = None) -> bytes:
    """Return xlsx bytes in the exact XHS export layout."""
    if rows is None:
        rows = [_one_row()]
    headers = [
        "笔记标题", "首次发布时间", "体裁",
        "曝光", "观看量", "封面点击率",
        "点赞", "评论", "收藏", "涨粉", "分享", "人均观看时长", "弹幕",
    ]
    banner_row = {h: "最多导出排序后前1000条笔记" for h in headers}
    header_row = {h: h for h in headers}
    all_rows = [banner_row, header_row] + rows
    df = pd.DataFrame(all_rows, columns=headers)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    return buf.getvalue()


# ─── TestXhsUploadEndpoint ────────────────────────────────────────────────────

class TestXhsUploadEndpoint:
    """POST /media/xhs/upload — basic upload contract."""

    def _post(self, client, token, account_id, rows=None):
        return client.post(
            "/media/xhs/upload",
            data={"account_id": account_id},
            files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(rows),
                            "application/octet-stream")},
            headers=_auth(token),
        )

    def test_valid_upload_returns_200(self, client, tokens, account):
        assert self._post(client, tokens["analyst"], account).status_code == 200

    def test_response_has_total_and_upserted(self, client, tokens, account):
        body = self._post(client, tokens["analyst"], account).json()
        assert "total" in body and "upserted" in body

    def test_total_matches_row_count(self, client, tokens, account):
        rows = [_one_row(**{"笔记标题": f"笔记{i}",
                            "首次发布时间": f"2026年0{i}月01日00时00分00秒"})
                for i in range(1, 4)]
        assert self._post(client, tokens["analyst"], account, rows).json()["total"] == 3

    def test_second_upload_is_idempotent(self, client, tokens, account):
        r1 = self._post(client, tokens["analyst"], account)
        r2 = self._post(client, tokens["analyst"], account)
        assert r1.status_code == r2.status_code == 200
        assert r2.json()["total"] == r1.json()["total"]

    def test_metrics_updated_on_re_upload(self, client, tokens, account):
        title, date_str = "更新测试笔记", "2026年03月15日00时00分00秒"
        self._post(client, tokens["analyst"], account,
                   [_one_row(**{"笔记标题": title, "首次发布时间": date_str, "曝光": "100"})])
        self._post(client, tokens["analyst"], account,
                   [_one_row(**{"笔记标题": title, "首次发布时间": date_str, "曝光": "999"})])
        posts = {p["title"]: p for p in
                 client.get("/media/xhs/posts", params={"account_id": account},
                            headers=_auth(tokens["analyst"])).json()}
        assert posts[title]["impressions"] == 999

    def test_absent_posts_preserved(self, client, tokens, account):
        old, new = "老笔记保留测试", "新笔记"
        self._post(client, tokens["analyst"], account,
                   [_one_row(**{"笔记标题": old, "首次发布时间": "2026年01月01日00时00分00秒"})])
        self._post(client, tokens["analyst"], account,
                   [_one_row(**{"笔记标题": new, "首次发布时间": "2026年02月01日00时00分00秒"})])
        titles = {p["title"] for p in
                  client.get("/media/xhs/posts", params={"account_id": account},
                             headers=_auth(tokens["analyst"])).json()}
        assert old in titles and new in titles

    def test_wrong_file_type_returns_400(self, client, tokens, account):
        r = client.post("/media/xhs/upload",
                        data={"account_id": account},
                        files={"file": ("data.csv", b"a,b,c\n1,2,3", "text/csv")},
                        headers=_auth(tokens["analyst"]))
        assert r.status_code == 400

    def test_empty_xlsx_returns_400(self, client, tokens, account):
        hdrs = ["笔记标题", "首次发布时间", "体裁",
                "曝光", "观看量", "封面点击率",
                "点赞", "评论", "收藏", "涨粉", "分享", "人均观看时长", "弹幕"]
        df = pd.DataFrame([{h: "最多导出排序后前1000条笔记" for h in hdrs}, {h: h for h in hdrs}],
                          columns=hdrs)
        buf = io.BytesIO()
        df.to_excel(buf, index=False, header=False)
        r = client.post("/media/xhs/upload",
                        data={"account_id": account},
                        files={"file": ("empty.xlsx", buf.getvalue(), "application/octet-stream")},
                        headers=_auth(tokens["analyst"]))
        assert r.status_code == 400


# ─── TestXhsPostsEndpoint ─────────────────────────────────────────────────────

class TestXhsPostsEndpoint:
    """GET /media/xhs/posts — listing and filtering, scoped to one account."""

    @pytest.fixture(autouse=True)
    def _seed(self, client, tokens, account):
        self.acc = account
        rows = [
            _one_row(**{"笔记标题": "一月文章", "首次发布时间": "2026年01月15日00时00分00秒",
                        "曝光": "1000", "点赞": "50"}),
            _one_row(**{"笔记标题": "三月文章", "首次发布时间": "2026年03月20日00时00分00秒",
                        "曝光": "500", "点赞": "20"}),
        ]
        client.post("/media/xhs/upload",
                    data={"account_id": account},
                    files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(rows),
                                    "application/octet-stream")},
                    headers=_auth(tokens["analyst"]))

    def _get(self, client, tokens, **params):
        params.setdefault("account_id", self.acc)
        return client.get("/media/xhs/posts", params=params,
                          headers=_auth(tokens["analyst"]))

    def test_returns_list(self, client, tokens):
        assert isinstance(self._get(client, tokens).json(), list)

    def test_all_expected_fields_present(self, client, tokens):
        row = self._get(client, tokens).json()[0]
        for f in ("id", "account_id", "title", "publish_date", "genre",
                  "impressions", "views", "cover_click_rate",
                  "likes", "comments", "collects", "new_followers",
                  "shares", "avg_watch_time", "danmu"):
            assert f in row, f"missing field: {f}"

    def test_start_date_filter(self, client, tokens):
        titles = [p["title"] for p in self._get(client, tokens,
                                                 start_date="2026-02-01").json()]
        assert "三月文章" in titles and "一月文章" not in titles

    def test_end_date_filter(self, client, tokens):
        titles = [p["title"] for p in self._get(client, tokens,
                                                 end_date="2026-02-01").json()]
        assert "一月文章" in titles and "三月文章" not in titles

    def test_date_range_filter(self, client, tokens):
        posts = self._get(client, tokens,
                          start_date="2026-01-01", end_date="2026-01-31").json()
        titles = [p["title"] for p in posts]
        assert "一月文章" in titles and "三月文章" not in titles
        for p in posts:
            assert p["publish_date"] <= "2026-01-31"

    def test_ordered_by_publish_date_desc(self, client, tokens):
        dates = [p["publish_date"] for p in self._get(client, tokens).json()]
        assert dates == sorted(dates, reverse=True)

    def test_numeric_fields_are_correct(self, client, tokens):
        row = next(p for p in self._get(client, tokens).json() if p["title"] == "一月文章")
        assert row["impressions"] == 1000 and row["likes"] == 50


# ─── TestXhsAuth ─────────────────────────────────────────────────────────────

class TestXhsAuth:
    """Authentication and role checks for XHS endpoints."""

    def test_upload_unauthenticated_returns_401(self, client, tokens, account):
        r = client.post("/media/xhs/upload",
                        data={"account_id": account},
                        files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(),
                                        "application/octet-stream")})
        assert r.status_code == 401

    def test_posts_unauthenticated_returns_401(self, client, tokens):
        assert client.get("/media/xhs/posts").status_code == 401

    def test_viewer_can_upload(self, client, tokens, account):
        r = client.post("/media/xhs/upload",
                        data={"account_id": account},
                        files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(),
                                        "application/octet-stream")},
                        headers=_auth(tokens["viewer"]))
        assert r.status_code == 200

    def test_viewer_cannot_list_posts(self, client, tokens):
        assert client.get("/media/xhs/posts",
                          headers=_auth(tokens["viewer"])).status_code == 403

    def test_analyst_can_list_posts(self, client, tokens, account):
        client.post("/media/xhs/upload",
                    data={"account_id": account},
                    files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(),
                                    "application/octet-stream")},
                    headers=_auth(tokens["analyst"]))
        assert client.get("/media/xhs/posts",
                          headers=_auth(tokens["analyst"])).status_code == 200

    def test_admin_can_upload_and_list(self, client, tokens, account):
        r_up = client.post("/media/xhs/upload",
                           data={"account_id": account},
                           files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(),
                                           "application/octet-stream")},
                           headers=_auth(tokens["admin"]))
        r_list = client.get("/media/xhs/posts", headers=_auth(tokens["admin"]))
        assert r_up.status_code == 200 and r_list.status_code == 200

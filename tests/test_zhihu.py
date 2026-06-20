"""Tests for Zhihu (知乎) CSV upload and post API.

Structure
─────────
Part 1 — ETL unit tests (no database, no HTTP)
  TestParseZhihuArticle    — parse_zhihu_csv() for 文章 (article) files
  TestParseZhihuQA         — parse_zhihu_csv() for 问答 (QA) files
  TestParseZhihuEdgeCases  — malformed / partial input

Part 2 — API integration tests (real test DB via pg_async_url)
  TestZhihuUploadEndpoint  — POST /media/zhihu/upload
  TestZhihuPostsEndpoint   — GET  /media/zhihu/posts
  TestZhihuAuth            — 401 / role checks
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest

# ── Part 1 — ETL unit tests (no DB) ───────────────────────────────────────────

from app.db.etl.zhihu import parse_zhihu_csv, _parse_zhihu_date, _int_or_none


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_article_df(rows: list[dict]) -> pd.DataFrame:
    headers = ["标题", "发布时间", "链接", "阅读", "点赞", "喜欢", "评论", "收藏", "分享"]
    return pd.DataFrame(rows, columns=headers)


def _make_qa_df(rows: list[dict]) -> pd.DataFrame:
    headers = ["标题", "发布时间", "链接", "阅读", "播放", "点赞", "喜欢", "评论", "收藏", "分享"]
    return pd.DataFrame(rows, columns=headers)


def _one_article(**overrides) -> dict:
    base = {
        "标题": "为什么睡眠对健康很重要？",
        "发布时间": "2026-05-31",
        "链接": "https://zhuanlan.zhihu.com/p/12345",
        "阅读": "100",
        "点赞": "10",
        "喜欢": "5",
        "评论": "3",
        "收藏": "7",
        "分享": "2",
    }
    base.update(overrides)
    return base


def _one_qa(**overrides) -> dict:
    base = {
        "标题": "咖啡和茶叶的咖啡因的含量？",
        "发布时间": "2026-05-31",
        "链接": "https://www.zhihu.com/answer/12345",
        "阅读": "50",
        "播放": "20",
        "点赞": "8",
        "喜欢": "3",
        "评论": "1",
        "收藏": "4",
        "分享": "1",
    }
    base.update(overrides)
    return base


# ─── TestParseZhihuArticle ────────────────────────────────────────────────────

class TestParseZhihuArticle:
    """parse_zhihu_csv() correctly maps all article columns."""

    def test_returns_list_of_dicts(self):
        df = _make_article_df([_one_article()])
        result = parse_zhihu_csv(df, "article")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_content_type_is_article(self):
        df = _make_article_df([_one_article()])
        assert parse_zhihu_csv(df, "article")[0]["content_type"] == "article"

    def test_title_preserved(self):
        df = _make_article_df([_one_article(**{"标题": "抗衰的本质是什么？"})])
        assert parse_zhihu_csv(df, "article")[0]["title"] == "抗衰的本质是什么？"

    def test_publish_date_parsed(self):
        df = _make_article_df([_one_article(**{"发布时间": "2025-12-31"})])
        assert parse_zhihu_csv(df, "article")[0]["publish_date"] == date(2025, 12, 31)

    def test_url_preserved(self):
        url = "https://zhuanlan.zhihu.com/p/99999"
        df = _make_article_df([_one_article(**{"链接": url})])
        assert parse_zhihu_csv(df, "article")[0]["url"] == url

    def test_numeric_fields_are_integers(self):
        df = _make_article_df([_one_article()])
        row = parse_zhihu_csv(df, "article")[0]
        for field in ("reads", "likes", "favorites", "comments", "collects", "shares"):
            assert isinstance(row[field], (int, type(None))), \
                f"{field} should be int, got {type(row[field])}"

    def test_reads_value(self):
        df = _make_article_df([_one_article(**{"阅读": "999"})])
        assert parse_zhihu_csv(df, "article")[0]["reads"] == 999

    def test_likes_value(self):
        df = _make_article_df([_one_article(**{"点赞": "42"})])
        assert parse_zhihu_csv(df, "article")[0]["likes"] == 42

    def test_plays_is_none_for_articles(self):
        df = _make_article_df([_one_article()])
        assert parse_zhihu_csv(df, "article")[0]["plays"] is None

    def test_required_keys_present(self):
        df = _make_article_df([_one_article()])
        row = parse_zhihu_csv(df, "article")[0]
        required = {"content_type", "title", "publish_date", "url",
                    "reads", "plays", "likes", "favorites", "comments", "collects", "shares"}
        assert required.issubset(row.keys())

    def test_zero_values_kept_not_nulled(self):
        df = _make_article_df([_one_article(**{"阅读": "0", "点赞": "0"})])
        row = parse_zhihu_csv(df, "article")[0]
        assert row["reads"] == 0
        assert row["likes"] == 0

    def test_multiple_rows_all_parsed(self):
        rows = [_one_article(**{"标题": f"文章{i}", "发布时间": f"2026-0{i}-01"})
                for i in range(1, 5)]
        df = _make_article_df(rows)
        assert len(parse_zhihu_csv(df, "article")) == 4

    def test_parses_real_article_example_file(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "data", "zhihu_article.xls")
        if not os.path.exists(path):
            pytest.skip("example file not present")
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        rows = parse_zhihu_csv(df, "article")
        assert len(rows) > 0
        assert rows[0]["content_type"] == "article"
        assert rows[0]["publish_date"] is not None


# ─── TestParseZhihuQA ─────────────────────────────────────────────────────────

class TestParseZhihuQA:
    """parse_zhihu_csv() correctly maps QA columns (including 播放)."""

    def test_content_type_is_qa(self):
        df = _make_qa_df([_one_qa()])
        assert parse_zhihu_csv(df, "qa")[0]["content_type"] == "qa"

    def test_plays_field_populated_for_qa(self):
        df = _make_qa_df([_one_qa(**{"播放": "77"})])
        assert parse_zhihu_csv(df, "qa")[0]["plays"] == 77

    def test_plays_zero_kept(self):
        df = _make_qa_df([_one_qa(**{"播放": "0"})])
        assert parse_zhihu_csv(df, "qa")[0]["plays"] == 0

    def test_all_numeric_fields_present(self):
        df = _make_qa_df([_one_qa()])
        row = parse_zhihu_csv(df, "qa")[0]
        for field in ("reads", "plays", "likes", "favorites", "comments", "collects", "shares"):
            assert field in row

    def test_parses_real_qa_example_file(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "data", "zhihu_qa.xls")
        if not os.path.exists(path):
            pytest.skip("example file not present")
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        rows = parse_zhihu_csv(df, "qa")
        assert len(rows) > 0
        assert rows[0]["content_type"] == "qa"
        assert rows[0]["plays"] is not None or rows[0]["plays"] == 0


# ─── TestParseZhihuEdgeCases ──────────────────────────────────────────────────

class TestParseZhihuEdgeCases:
    """parse_zhihu_csv handles malformed / partial input gracefully."""

    def test_empty_title_skipped(self):
        df = _make_article_df([_one_article(**{"标题": ""})])
        assert parse_zhihu_csv(df, "article") == []

    def test_whitespace_title_skipped(self):
        df = _make_article_df([_one_article(**{"标题": "   "})])
        assert parse_zhihu_csv(df, "article") == []

    def test_bad_date_skipped(self):
        df = _make_article_df([_one_article(**{"发布时间": "not-a-date"})])
        assert parse_zhihu_csv(df, "article") == []

    def test_none_date_skipped(self):
        df = _make_article_df([_one_article(**{"发布时间": None})])
        assert parse_zhihu_csv(df, "article") == []

    def test_non_numeric_metric_becomes_none(self):
        df = _make_article_df([_one_article(**{"阅读": "N/A", "点赞": "--"})])
        row = parse_zhihu_csv(df, "article")[0]
        assert row["reads"] is None
        assert row["likes"] is None

    def test_empty_url_becomes_none(self):
        df = _make_article_df([_one_article(**{"链接": ""})])
        assert parse_zhihu_csv(df, "article")[0]["url"] is None

    def test_mixed_valid_invalid_rows(self):
        rows = [
            _one_article(**{"标题": "有效文章"}),
            _one_article(**{"标题": ""}),                      # empty title → skip
            _one_article(**{"发布时间": "bad-date"}),          # bad date → skip
            _one_article(**{"标题": "另一篇", "发布时间": "2026-04-01"}),
        ]
        df = _make_article_df(rows)
        result = parse_zhihu_csv(df, "article")
        assert len(result) == 2
        assert result[0]["title"] == "有效文章"
        assert result[1]["title"] == "另一篇"

    def test_empty_dataframe_returns_empty_list(self):
        df = _make_article_df([])
        assert parse_zhihu_csv(df, "article") == []


# ─── TestZhihuDateHelper ──────────────────────────────────────────────────────

class TestZhihuDateHelper:
    """_parse_zhihu_date handles YYYY-MM-DD and edge cases."""

    def test_standard_format(self):
        assert _parse_zhihu_date("2026-05-31") == date(2026, 5, 31)

    def test_none_input(self):
        assert _parse_zhihu_date(None) is None

    def test_invalid_returns_none(self):
        assert _parse_zhihu_date("not-a-date") is None

    def test_empty_string_returns_none(self):
        assert _parse_zhihu_date("") is None


# ── Part 2 — API integration tests ────────────────────────────────────────────

from test_api_endpoints import client, tokens  # noqa: F401


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_article_csv_bytes(rows: list[dict] | None = None) -> bytes:
    if rows is None:
        rows = [_one_article()]
    headers = ["标题", "发布时间", "链接", "阅读", "点赞", "喜欢", "评论", "收藏", "分享"]
    df = pd.DataFrame(rows, columns=headers)
    buf = io.BytesIO()
    buf.write("﻿".encode("utf-8"))
    buf.write(df.to_csv(index=False).encode("utf-8"))
    return buf.getvalue()


def _make_qa_csv_bytes(rows: list[dict] | None = None) -> bytes:
    if rows is None:
        rows = [_one_qa()]
    headers = ["标题", "发布时间", "链接", "阅读", "播放", "点赞", "喜欢", "评论", "收藏", "分享"]
    df = pd.DataFrame(rows, columns=headers)
    buf = io.BytesIO()
    buf.write("﻿".encode("utf-8"))
    buf.write(df.to_csv(index=False).encode("utf-8"))
    return buf.getvalue()


def _post_upload(client, token, content_type: str, file_bytes: bytes, filename="data.csv"):
    return client.post(
        "/media/zhihu/upload",
        data={"content_type": content_type},
        files={"file": (filename, file_bytes, "application/octet-stream")},
        headers=_auth(token),
    )


# ─── TestZhihuUploadEndpoint ──────────────────────────────────────────────────

class TestZhihuUploadEndpoint:
    """POST /media/zhihu/upload — basic upload contract."""

    def test_article_upload_returns_200(self, client, tokens):
        r = _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes())
        assert r.status_code == 200

    def test_qa_upload_returns_200(self, client, tokens):
        r = _post_upload(client, tokens["analyst"], "qa", _make_qa_csv_bytes())
        assert r.status_code == 200

    def test_response_has_total_and_upserted(self, client, tokens):
        body = _post_upload(client, tokens["analyst"], "article",
                            _make_article_csv_bytes()).json()
        assert "total" in body and "upserted" in body

    def test_total_matches_row_count(self, client, tokens):
        rows = [_one_article(**{"标题": f"文章{i}", "发布时间": f"2026-0{i}-01"})
                for i in range(1, 4)]
        body = _post_upload(client, tokens["analyst"], "article",
                            _make_article_csv_bytes(rows)).json()
        assert body["total"] == 3

    def test_second_upload_is_idempotent(self, client, tokens):
        data = _make_article_csv_bytes()
        r1 = _post_upload(client, tokens["analyst"], "article", data)
        r2 = _post_upload(client, tokens["analyst"], "article", data)
        assert r1.status_code == r2.status_code == 200
        assert r2.json()["total"] == r1.json()["total"]

    def test_metrics_updated_on_re_upload(self, client, tokens):
        row = _one_article(**{"标题": "更新测试", "发布时间": "2026-03-15", "阅读": "100"})
        _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes([row]))
        row["阅读"] = "999"
        _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes([row]))
        posts = {p["title"]: p for p in
                 client.get("/media/zhihu/posts",
                            params={"content_type": "article"},
                            headers=_auth(tokens["analyst"])).json()}
        assert posts["更新测试"]["reads"] == 999

    def test_absent_posts_preserved(self, client, tokens):
        old = _one_article(**{"标题": "老文章保留", "发布时间": "2026-01-01"})
        new = _one_article(**{"标题": "新文章", "发布时间": "2026-02-01"})
        _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes([old]))
        _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes([new]))
        titles = {p["title"] for p in
                  client.get("/media/zhihu/posts", params={"content_type": "article"},
                             headers=_auth(tokens["analyst"])).json()}
        assert "老文章保留" in titles and "新文章" in titles

    def test_invalid_content_type_returns_422(self, client, tokens):
        r = _post_upload(client, tokens["analyst"], "invalid_type",
                         _make_article_csv_bytes())
        assert r.status_code == 422

    def test_empty_file_returns_400(self, client, tokens):
        headers = ["标题", "发布时间", "链接", "阅读", "点赞", "喜欢", "评论", "收藏", "分享"]
        buf = io.BytesIO()
        buf.write("﻿".encode("utf-8"))
        buf.write(pd.DataFrame(columns=headers).to_csv(index=False).encode("utf-8"))
        r = _post_upload(client, tokens["analyst"], "article", buf.getvalue())
        assert r.status_code == 400

    def test_viewer_can_upload(self, client, tokens):
        r = _post_upload(client, tokens["viewer"], "article", _make_article_csv_bytes())
        assert r.status_code == 200

    def test_article_and_qa_stored_separately(self, client, tokens):
        article = _one_article(**{"标题": "同名内容", "发布时间": "2026-01-01"})
        qa = _one_qa(**{"标题": "同名内容", "发布时间": "2026-01-01"})
        _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes([article]))
        _post_upload(client, tokens["analyst"], "qa", _make_qa_csv_bytes([qa]))
        articles = client.get("/media/zhihu/posts", params={"content_type": "article"},
                              headers=_auth(tokens["analyst"])).json()
        qas = client.get("/media/zhihu/posts", params={"content_type": "qa"},
                         headers=_auth(tokens["analyst"])).json()
        assert any(p["title"] == "同名内容" for p in articles)
        assert any(p["title"] == "同名内容" for p in qas)


# ─── TestZhihuPostsEndpoint ───────────────────────────────────────────────────

class TestZhihuPostsEndpoint:
    """GET /media/zhihu/posts — listing and filtering."""

    @pytest.fixture(autouse=True)
    def _seed(self, client, tokens):
        rows = [
            _one_article(**{"标题": "一月文章", "发布时间": "2026-01-15", "阅读": "1000"}),
            _one_article(**{"标题": "三月文章", "发布时间": "2026-03-20", "阅读": "500"}),
        ]
        _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes(rows))
        qa_rows = [_one_qa(**{"标题": "一月问答", "发布时间": "2026-01-10", "播放": "30"})]
        _post_upload(client, tokens["analyst"], "qa", _make_qa_csv_bytes(qa_rows))

    def _get(self, client, tokens, **params):
        return client.get("/media/zhihu/posts", params=params,
                          headers=_auth(tokens["analyst"]))

    def test_returns_list(self, client, tokens):
        assert isinstance(self._get(client, tokens, content_type="article").json(), list)

    def test_article_fields_present(self, client, tokens):
        row = self._get(client, tokens, content_type="article").json()[0]
        for f in ("id", "content_type", "title", "publish_date", "url",
                  "reads", "plays", "likes", "favorites", "comments", "collects", "shares"):
            assert f in row, f"missing field: {f}"

    def test_filter_by_content_type_article(self, client, tokens):
        posts = self._get(client, tokens, content_type="article").json()
        assert all(p["content_type"] == "article" for p in posts)

    def test_filter_by_content_type_qa(self, client, tokens):
        posts = self._get(client, tokens, content_type="qa").json()
        assert all(p["content_type"] == "qa" for p in posts)

    def test_start_date_filter(self, client, tokens):
        titles = [p["title"] for p in
                  self._get(client, tokens, content_type="article",
                            start_date="2026-02-01").json()]
        assert "三月文章" in titles and "一月文章" not in titles

    def test_end_date_filter(self, client, tokens):
        titles = [p["title"] for p in
                  self._get(client, tokens, content_type="article",
                            end_date="2026-02-01").json()]
        assert "一月文章" in titles and "三月文章" not in titles

    def test_ordered_by_publish_date_desc(self, client, tokens):
        dates = [p["publish_date"] for p in
                 self._get(client, tokens, content_type="article").json()]
        assert dates == sorted(dates, reverse=True)

    def test_reads_value_correct(self, client, tokens):
        row = next(p for p in self._get(client, tokens, content_type="article").json()
                   if p["title"] == "一月文章")
        assert row["reads"] == 1000

    def test_qa_plays_field_populated(self, client, tokens):
        row = self._get(client, tokens, content_type="qa").json()[0]
        assert row["plays"] is not None

    def test_no_content_type_filter_returns_all(self, client, tokens):
        posts = self._get(client, tokens).json()
        types = {p["content_type"] for p in posts}
        assert "article" in types and "qa" in types


# ─── TestZhihuAuth ────────────────────────────────────────────────────────────

class TestZhihuAuth:
    """Authentication and role checks for Zhihu endpoints."""

    def test_upload_unauthenticated_returns_401(self, client, tokens):
        r = client.post(
            "/media/zhihu/upload",
            data={"content_type": "article"},
            files={"file": ("data.csv", _make_article_csv_bytes(), "application/octet-stream")},
        )
        assert r.status_code == 401

    def test_posts_unauthenticated_returns_401(self, client, tokens):
        assert client.get("/media/zhihu/posts").status_code == 401

    def test_viewer_can_upload(self, client, tokens):
        r = _post_upload(client, tokens["viewer"], "article", _make_article_csv_bytes())
        assert r.status_code == 200

    def test_viewer_cannot_list_posts(self, client, tokens):
        assert self._get_posts(client, tokens["viewer"]).status_code == 403

    def test_analyst_can_list_posts(self, client, tokens):
        _post_upload(client, tokens["analyst"], "article", _make_article_csv_bytes())
        assert self._get_posts(client, tokens["analyst"]).status_code == 200

    def test_admin_can_upload_and_list(self, client, tokens):
        r_up = _post_upload(client, tokens["admin"], "article", _make_article_csv_bytes())
        r_list = self._get_posts(client, tokens["admin"])
        assert r_up.status_code == 200 and r_list.status_code == 200

    def _get_posts(self, client, token):
        return client.get("/media/zhihu/posts",
                          headers={"Authorization": f"Bearer {token}"})

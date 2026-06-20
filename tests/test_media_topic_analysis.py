"""Tests for WeChat media topic analysis features.

Covers four analysis features:
  层次一 — Traffic source breakdown (read_user_source aggregation + /media/source-breakdown endpoint)
  层次三 — Read-completion × engagement matrix (read_finish_rate on /media/posts)
  层次二 — Topic keyword grouping (pure Python analysis helper)
  交叉   — Topic × source cross matrix (/media/source-by-post + build_topic_source_matrix)
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
# Pure-Python unit tests — no DB required
# ─────────────────────────────────────────────────────────────────────────────

from app.views.media.analysis import aggregate_read_sources, build_topic_source_matrix, match_article_topics


class TestAggregateReadSources:
    def test_basic_aggregation_sums_by_scene(self):
        sources = [
            [{"user_count": 30, "scene_desc": "朋友圈"}, {"user_count": 10, "scene_desc": "搜一搜"}, {"user_count": 40, "scene_desc": "全部"}],
            [{"user_count": 20, "scene_desc": "朋友圈"}, {"user_count": 5, "scene_desc": "订阅号"}, {"user_count": 25, "scene_desc": "全部"}],
        ]
        result = aggregate_read_sources(sources)
        assert result["朋友圈"] == 50
        assert result["搜一搜"] == 10
        assert result["订阅号"] == 5

    def test_excludes_quanbu_total_entry(self):
        sources = [
            [{"user_count": 100, "scene_desc": "全部"}, {"user_count": 60, "scene_desc": "朋友圈"}],
        ]
        result = aggregate_read_sources(sources)
        assert "全部" not in result
        assert result["朋友圈"] == 60

    def test_empty_list_returns_empty_dict(self):
        assert aggregate_read_sources([]) == {}

    def test_none_entries_are_skipped(self):
        sources = [None, [{"user_count": 15, "scene_desc": "搜一搜"}], None]
        result = aggregate_read_sources(sources)
        assert result == {"搜一搜": 15}

    def test_empty_inner_lists_are_skipped(self):
        sources = [[], [{"user_count": 8, "scene_desc": "好友转发"}]]
        result = aggregate_read_sources(sources)
        assert result == {"好友转发": 8}

    def test_accumulates_same_scene_across_days(self):
        day1 = [{"user_count": 100, "scene_desc": "朋友圈"}]
        day2 = [{"user_count": 200, "scene_desc": "朋友圈"}]
        day3 = [{"user_count": 50, "scene_desc": "朋友圈"}]
        result = aggregate_read_sources([day1, day2, day3])
        assert result["朋友圈"] == 350

    def test_all_quanbu_returns_empty(self):
        sources = [
            [{"user_count": 100, "scene_desc": "全部"}],
            [{"user_count": 200, "scene_desc": "全部"}],
        ]
        assert aggregate_read_sources(sources) == {}

    def test_missing_scene_desc_falls_back_to_unknown(self):
        sources = [[{"user_count": 5}]]
        result = aggregate_read_sources(sources)
        assert result.get("未知") == 5


class TestMatchArticleTopics:
    def test_basic_keyword_match(self):
        result = match_article_topics("最新新品上市发布", {"新品": ["新品", "上市"]})
        assert "新品" in result

    def test_no_match_returns_other(self):
        result = match_article_topics("春日生活记录", {"新品": ["新品", "上市"]})
        assert result == ["其他"]

    def test_multiple_topics_can_match(self):
        topic_map = {
            "促销": ["折扣", "特惠", "优惠"],
            "新品": ["新品", "上市", "发布"],
        }
        result = match_article_topics("新品特惠折扣大促", topic_map)
        assert "促销" in result
        assert "新品" in result
        assert "其他" not in result

    def test_case_insensitive_matching(self):
        result = match_article_topics("Summer SALE limited", {"sale": ["sale"]})
        assert "sale" in result

    def test_empty_title_returns_other(self):
        result = match_article_topics("", {"新品": ["新品"]})
        assert result == ["其他"]

    def test_empty_topic_map_returns_other(self):
        result = match_article_topics("很好的文章", {})
        assert result == ["其他"]

    def test_partial_keyword_match_counts(self):
        result = match_article_topics("行业深度报告：2026趋势", {"行业报告": ["行业", "报告"]})
        assert "行业报告" in result


# ─────────────────────────────────────────────────────────────────────────────
# API integration tests — require PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────

def _clear_wechat_env(monkeypatch):
    for key in ["WECHAT_OFFICIAL_APP_ID", "WECHAT_OFFICIAL_APP_SECRET", "WECHAT_OFFICIAL_ACCOUNT_NAME"]:
        monkeypatch.delenv(key, raising=False)
    for idx in range(1, 11):
        monkeypatch.delenv(f"WECHAT_APP_ID_{idx}", raising=False)
        monkeypatch.delenv(f"WECHAT_APP_SECRET_{idx}", raising=False)
        monkeypatch.delenv(f"WECHAT_ACCOUNT_NAME_{idx}", raising=False)


def _sync_articles(client, tokens, monkeypatch, articles: list[dict]):
    """Helper: sync a list of fake article rows via the WeChat sync endpoint."""
    _clear_wechat_env(monkeypatch)
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_ID", "wx-test")
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_SECRET", "secret-test")
    monkeypatch.setenv("WECHAT_OFFICIAL_ACCOUNT_NAME", "Test Account")

    import app.views.media.routes as media_view

    class FakeClient:
        def __init__(self, app_id, app_secret):
            pass
        def fetch_article_total_rows(self, start_date, end_date):
            return articles

    monkeypatch.setattr(media_view, "WeChatOfficialClient", FakeClient)

    r = client.post(
        "/media/wechat/sync",
        json={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 200


# ── 层次一: /media/source-breakdown ─────────────────────────────────────────

def test_source_breakdown_aggregates_across_posts(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "src-1",
            "title": "Article about 朋友圈 growth",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 10),
            "url": None,
            "read_user_count": 120,
            "share_user_count": 5,
            "like_user": 2,
            "comment_count": 0,
            "collection_user": 1,
            "read_avg_time": 1.2,
            "read_user_source": [
                {"user_count": 80, "scene_desc": "朋友圈"},
                {"user_count": 30, "scene_desc": "搜一搜"},
                {"user_count": 120, "scene_desc": "全部"},
            ],
            "read_finish_rate": 0.55,
            "raw_payload": {},
        },
        {
            "external_id": "src-2",
            "title": "Article about search optimization",
            "publish_date": date(2026, 5, 12),
            "metric_date": date(2026, 5, 12),
            "url": None,
            "read_user_count": 60,
            "share_user_count": 2,
            "like_user": 1,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": 0.8,
            "read_user_source": [
                {"user_count": 50, "scene_desc": "搜一搜"},
                {"user_count": 10, "scene_desc": "订阅号"},
                {"user_count": 60, "scene_desc": "全部"},
            ],
            "read_finish_rate": 0.40,
            "raw_payload": {},
        },
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/source-breakdown",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["朋友圈"] == 80
    assert data["搜一搜"] == 80  # 30 + 50
    assert data["订阅号"] == 10
    assert "全部" not in data


def test_source_breakdown_filters_by_date(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "date-filter-1",
            "title": "Early May article",
            "publish_date": date(2026, 5, 5),
            "metric_date": date(2026, 5, 5),
            "url": None,
            "read_user_count": 50,
            "share_user_count": 1,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [{"user_count": 40, "scene_desc": "朋友圈"}, {"user_count": 50, "scene_desc": "全部"}],
            "read_finish_rate": None,
            "raw_payload": {},
        },
        {
            "external_id": "date-filter-2",
            "title": "Late May article",
            "publish_date": date(2026, 5, 25),
            "metric_date": date(2026, 5, 25),
            "url": None,
            "read_user_count": 30,
            "share_user_count": 1,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [{"user_count": 20, "scene_desc": "搜一搜"}, {"user_count": 30, "scene_desc": "全部"}],
            "read_finish_rate": None,
            "raw_payload": {},
        },
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/source-breakdown",
        params={"start_date": "2026-05-20", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    assert "朋友圈" not in data
    assert data.get("搜一搜", 0) >= 20


def test_source_breakdown_returns_empty_when_no_source_data(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "no-src-1",
            "title": "No source data article",
            "publish_date": date(2026, 5, 15),
            "metric_date": date(2026, 5, 15),
            "url": None,
            "read_user_count": 10,
            "share_user_count": 0,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": None,
            "read_finish_rate": None,
            "raw_payload": {},
        }
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/source-breakdown",
        params={"start_date": "2026-05-15", "end_date": "2026-05-15"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    assert r.json() == {}


def test_source_breakdown_requires_auth(client):
    r = client.get("/media/source-breakdown")
    assert r.status_code == 401


def test_source_breakdown_forbidden_for_viewer(client, tokens):
    r = client.get("/media/source-breakdown", headers=_auth(tokens["viewer"]))
    assert r.status_code == 403


# ── 层次三: read_finish_rate in /media/posts ─────────────────────────────────

def test_posts_endpoint_includes_read_finish_rate(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "finish-rate-1",
            "title": "Article with finish rate",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 10),
            "url": None,
            "read_user_count": 100,
            "share_user_count": 4,
            "like_user": 2,
            "comment_count": 0,
            "collection_user": 1,
            "read_avg_time": 1.5,
            "read_user_source": None,
            "read_finish_rate": 0.72,
            "raw_payload": {},
        }
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/posts",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    rows = r.json()
    matched = [row for row in rows if row["title"] == "Article with finish rate"]
    assert len(matched) == 1
    assert "read_finish_rate" in matched[0]
    assert matched[0]["read_finish_rate"] == pytest.approx(0.72, abs=0.01)


def test_posts_endpoint_finish_rate_averaged_across_metric_days(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "avg-finish-1",
            "title": "Multi-day finish rate article",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 10),
            "url": None,
            "read_user_count": 100,
            "share_user_count": 3,
            "like_user": 1,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": 1.0,
            "read_user_source": None,
            "read_finish_rate": 0.60,
            "raw_payload": {},
        },
        {
            "external_id": "avg-finish-1",  # same article, different day
            "title": "Multi-day finish rate article",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 11),
            "url": None,
            "read_user_count": 20,
            "share_user_count": 1,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": 1.0,
            "read_user_source": None,
            "read_finish_rate": 0.40,
            "raw_payload": {},
        },
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/posts",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    rows = r.json()
    matched = [row for row in rows if row["title"] == "Multi-day finish rate article"]
    assert len(matched) == 1
    rate = matched[0]["read_finish_rate"]
    assert rate is not None
    assert 0.40 <= rate <= 0.60  # average of 0.60 and 0.40 = 0.50


def test_posts_endpoint_finish_rate_is_none_when_not_recorded(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "no-finish-1",
            "title": "Article without finish rate",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 10),
            "url": None,
            "read_user_count": 50,
            "share_user_count": 1,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": None,
            "read_finish_rate": None,
            "raw_payload": {},
        }
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/posts",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    rows = r.json()
    matched = [row for row in rows if row["title"] == "Article without finish rate"]
    assert len(matched) == 1
    assert matched[0]["read_finish_rate"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 交叉分析: build_topic_source_matrix — pure Python
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTopicSourceMatrix:
    _topic_map = {
        "新品发布": ["新品", "上市", "发布"],
        "行业报告": ["行业", "报告", "趋势"],
    }

    def _post(self, pid: int, title: str) -> dict:
        return {"id": pid, "title": title}

    def test_basic_single_topic_single_source(self):
        posts = [self._post(1, "新品上市快报")]
        sources = {"1": {"朋友圈": 80, "搜一搜": 20}}
        result = build_topic_source_matrix(posts, sources, self._topic_map)
        assert result["新品发布"]["朋友圈"] == 80
        assert result["新品发布"]["搜一搜"] == 20
        assert "行业报告" not in result

    def test_accumulates_source_counts_across_multiple_posts_same_topic(self):
        posts = [self._post(1, "新品发布预告"), self._post(2, "新品上市公告")]
        sources = {
            "1": {"朋友圈": 50, "搜一搜": 10},
            "2": {"朋友圈": 30, "订阅号": 20},
        }
        result = build_topic_source_matrix(posts, sources, self._topic_map)
        assert result["新品发布"]["朋友圈"] == 80   # 50 + 30
        assert result["新品发布"]["搜一搜"] == 10
        assert result["新品发布"]["订阅号"] == 20

    def test_article_matching_multiple_topics_counted_in_all(self):
        posts = [self._post(1, "行业新品发布报告")]  # matches both topics
        sources = {"1": {"搜一搜": 40}}
        result = build_topic_source_matrix(posts, sources, self._topic_map)
        assert result["新品发布"]["搜一搜"] == 40
        assert result["行业报告"]["搜一搜"] == 40

    def test_unmatched_article_goes_to_other_bucket(self):
        posts = [self._post(1, "春日生活日记")]
        sources = {"1": {"朋友圈": 60}}
        result = build_topic_source_matrix(posts, sources, self._topic_map)
        assert "其他" in result
        assert result["其他"]["朋友圈"] == 60

    def test_post_with_no_source_data_is_skipped(self):
        posts = [self._post(1, "新品发布"), self._post(2, "行业报告精读")]
        sources = {"2": {"搜一搜": 30}}  # post 1 has no source entry
        result = build_topic_source_matrix(posts, sources, self._topic_map)
        assert "新品发布" not in result
        assert result["行业报告"]["搜一搜"] == 30

    def test_post_with_empty_source_dict_is_skipped(self):
        posts = [self._post(1, "新品上市")]
        sources = {"1": {}}
        result = build_topic_source_matrix(posts, sources, self._topic_map)
        assert "新品发布" not in result

    def test_empty_posts_list_returns_empty_matrix(self):
        result = build_topic_source_matrix([], {}, self._topic_map)
        assert result == {}

    def test_empty_topic_map_all_posts_go_to_other(self):
        posts = [self._post(1, "新品上市快报"), self._post(2, "行业趋势报告")]
        sources = {"1": {"朋友圈": 10}, "2": {"搜一搜": 20}}
        result = build_topic_source_matrix(posts, sources, {})
        assert result["其他"]["朋友圈"] == 10
        assert result["其他"]["搜一搜"] == 20

    def test_multiple_topics_with_multiple_sources(self):
        posts = [
            self._post(1, "新品上市活动"),
            self._post(2, "2026行业趋势报告"),
            self._post(3, "促销折扣活动"),  # no match → 其他
        ]
        sources = {
            "1": {"朋友圈": 100, "好友转发": 20},
            "2": {"搜一搜": 80, "订阅号": 30},
            "3": {"朋友圈": 15},
        }
        result = build_topic_source_matrix(posts, sources, self._topic_map)
        assert result["新品发布"]["朋友圈"] == 100
        assert result["新品发布"]["好友转发"] == 20
        assert result["行业报告"]["搜一搜"] == 80
        assert result["行业报告"]["订阅号"] == 30
        assert result["其他"]["朋友圈"] == 15


# ─────────────────────────────────────────────────────────────────────────────
# 交叉分析: /media/source-by-post API endpoint
# ─────────────────────────────────────────────────────────────────────────────

def test_source_by_post_returns_per_post_breakdown(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "sbp-1",
            "title": "Article A source by post",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 10),
            "url": None,
            "read_user_count": 100,
            "share_user_count": 3,
            "like_user": 1,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [
                {"user_count": 60, "scene_desc": "朋友圈"},
                {"user_count": 40, "scene_desc": "搜一搜"},
                {"user_count": 100, "scene_desc": "全部"},
            ],
            "read_finish_rate": None,
            "raw_payload": {},
        },
        {
            "external_id": "sbp-2",
            "title": "Article B source by post",
            "publish_date": date(2026, 5, 11),
            "metric_date": date(2026, 5, 11),
            "url": None,
            "read_user_count": 50,
            "share_user_count": 1,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [
                {"user_count": 45, "scene_desc": "订阅号"},
                {"user_count": 50, "scene_desc": "全部"},
            ],
            "read_finish_rate": None,
            "raw_payload": {},
        },
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/source-by-post",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()

    # Each value is {scene: count}, keyed by str(post_id)
    all_sources = list(data.values())
    all_scenes = {scene for s in all_sources for scene in s}
    assert "朋友圈" in all_scenes
    assert "搜一搜" in all_scenes
    assert "订阅号" in all_scenes
    assert "全部" not in all_scenes

    # Each post should have its own separate breakdown
    assert len(data) >= 2


def test_source_by_post_aggregates_multiple_metric_days_per_post(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "sbp-multiday",
            "title": "Multiday source post",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 10),
            "url": None,
            "read_user_count": 80,
            "share_user_count": 2,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [{"user_count": 50, "scene_desc": "朋友圈"}, {"user_count": 80, "scene_desc": "全部"}],
            "read_finish_rate": None,
            "raw_payload": {},
        },
        {
            "external_id": "sbp-multiday",  # same article, next day
            "title": "Multiday source post",
            "publish_date": date(2026, 5, 10),
            "metric_date": date(2026, 5, 11),
            "url": None,
            "read_user_count": 20,
            "share_user_count": 1,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [{"user_count": 15, "scene_desc": "朋友圈"}, {"user_count": 20, "scene_desc": "全部"}],
            "read_finish_rate": None,
            "raw_payload": {},
        },
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/source-by-post",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    # The two metric rows belong to the same post — should be merged
    post_sources = list(data.values())
    matched = [s for s in post_sources if s.get("朋友圈", 0) >= 60]  # 50 + 15 = 65
    assert len(matched) == 1
    assert matched[0]["朋友圈"] == 65


def test_source_by_post_filters_by_date(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "sbp-early",
            "title": "Early post source",
            "publish_date": date(2026, 5, 1),
            "metric_date": date(2026, 5, 1),
            "url": None,
            "read_user_count": 30,
            "share_user_count": 0,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [{"user_count": 25, "scene_desc": "朋友圈"}, {"user_count": 30, "scene_desc": "全部"}],
            "read_finish_rate": None,
            "raw_payload": {},
        },
        {
            "external_id": "sbp-late",
            "title": "Late post source",
            "publish_date": date(2026, 5, 20),
            "metric_date": date(2026, 5, 20),
            "url": None,
            "read_user_count": 20,
            "share_user_count": 0,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": [{"user_count": 18, "scene_desc": "搜一搜"}, {"user_count": 20, "scene_desc": "全部"}],
            "read_finish_rate": None,
            "raw_payload": {},
        },
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/source-by-post",
        params={"start_date": "2026-05-15", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    all_scenes = {scene for s in data.values() for scene in s}
    assert "搜一搜" in all_scenes
    assert "朋友圈" not in all_scenes


def test_source_by_post_skips_null_source_rows(client, tokens, monkeypatch):
    articles = [
        {
            "external_id": "sbp-null-src",
            "title": "Post with null sources",
            "publish_date": date(2026, 5, 5),
            "metric_date": date(2026, 5, 5),
            "url": None,
            "read_user_count": 10,
            "share_user_count": 0,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_time": None,
            "read_user_source": None,
            "read_finish_rate": None,
            "raw_payload": {},
        }
    ]
    _sync_articles(client, tokens, monkeypatch, articles)

    r = client.get(
        "/media/source-by-post",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    # Post with all-null sources should not appear in the response
    assert r.json() == {}


def test_source_by_post_forbidden_for_viewer(client, tokens):
    r = client.get("/media/source-by-post", headers=_auth(tokens["viewer"]))
    assert r.status_code == 403

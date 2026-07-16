"""Tests for content × ecommerce correlation analysis.

Covers:
  Pure Python — compute_content_impact()
  API         — GET /media/content-impact
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401
from app.views.media.analysis import compute_content_impact


# ─────────────────────────────────────────────────────────────────────────────
# Pure-Python unit tests — no DB
# ─────────────────────────────────────────────────────────────────────────────

def _daily(orders: int, revenue: float = 0.0) -> dict:
    return {"orders": orders, "revenue": revenue}


class TestComputeContentImpact:

    def test_positive_order_lift(self):
        posts = [{"id": 1, "title": "Article A", "publish_date": "2026-05-15",
                  "read_user_count": 500, "share_user_count": 10}]
        # pre-window: 5/8–5/14 → 3 orders; post-window: 5/15–5/21 → 6 orders
        daily = {
            "2026-05-08": _daily(1), "2026-05-09": _daily(1), "2026-05-10": _daily(1),
            "2026-05-15": _daily(2), "2026-05-16": _daily(2), "2026-05-17": _daily(2),
        }
        result = compute_content_impact(posts, daily, window_days=7)
        assert len(result) == 1
        r = result[0]
        assert r["pre_orders"] == 3
        assert r["post_orders"] == 6
        assert r["order_lift_pct"] == pytest.approx(100.0)

    def test_negative_order_lift(self):
        posts = [{"id": 1, "title": "Article B", "publish_date": "2026-05-15",
                  "read_user_count": 200, "share_user_count": 5}]
        daily = {
            "2026-05-08": _daily(10), "2026-05-15": _daily(5),
        }
        result = compute_content_impact(posts, daily, window_days=7)
        r = result[0]
        assert r["order_lift_pct"] < 0

    def test_no_baseline_lift_is_none(self):
        posts = [{"id": 1, "title": "New launch", "publish_date": "2026-05-15",
                  "read_user_count": 100, "share_user_count": 2}]
        daily = {"2026-05-15": _daily(10)}  # no pre-window data
        result = compute_content_impact(posts, daily, window_days=7)
        assert result[0]["order_lift_pct"] is None
        assert result[0]["revenue_lift_pct"] is None
        assert result[0]["post_orders"] == 10

    def test_publish_date_is_in_post_window(self):
        """Day 0 (publish_date) must be included in the post window, not pre."""
        posts = [{"id": 1, "title": "T", "publish_date": "2026-05-15",
                  "read_user_count": 100, "share_user_count": 0}]
        daily = {"2026-05-14": _daily(5), "2026-05-15": _daily(10)}
        result = compute_content_impact(posts, daily, window_days=7)
        r = result[0]
        assert r["pre_orders"] == 5   # only 5/14
        assert r["post_orders"] == 10  # only 5/15

    def test_revenue_lift_computed_independently(self):
        posts = [{"id": 1, "title": "T", "publish_date": "2026-05-15",
                  "read_user_count": 100, "share_user_count": 0}]
        daily = {
            "2026-05-08": _daily(1, revenue=100.0),
            "2026-05-15": _daily(1, revenue=300.0),
        }
        result = compute_content_impact(posts, daily, window_days=7)
        r = result[0]
        assert r["pre_revenue"] == pytest.approx(100.0)
        assert r["post_revenue"] == pytest.approx(300.0)
        assert r["revenue_lift_pct"] == pytest.approx(200.0)

    def test_sorted_by_lift_descending(self):
        # Use non-overlapping date ranges to avoid cross-contamination of windows.
        # Post1: publish 2026-05-15 (window7): pre=5/8, post=5/15 → +10%
        # Post2: publish 2026-05-29 (window7): pre=5/22, post=5/29 → +100%
        posts = [
            {"id": 1, "title": "Low", "publish_date": "2026-05-15",
             "read_user_count": 100, "share_user_count": 0},
            {"id": 2, "title": "High", "publish_date": "2026-05-29",
             "read_user_count": 200, "share_user_count": 0},
        ]
        daily = {
            "2026-05-08": _daily(10), "2026-05-15": _daily(11),  # post1: +10%
            "2026-05-22": _daily(10), "2026-05-29": _daily(20),  # post2: +100%
        }
        result = compute_content_impact(posts, daily, window_days=7)
        assert result[0]["title"] == "High"
        assert result[1]["title"] == "Low"

    def test_empty_posts_returns_empty_list(self):
        assert compute_content_impact([], {}, window_days=7) == []

    def test_window_days_1_only_publish_day_in_post(self):
        posts = [{"id": 1, "title": "T", "publish_date": "2026-05-15",
                  "read_user_count": 50, "share_user_count": 0}]
        daily = {"2026-05-14": _daily(5), "2026-05-15": _daily(9)}
        result = compute_content_impact(posts, daily, window_days=1)
        r = result[0]
        assert r["pre_orders"] == 5
        assert r["post_orders"] == 9

    def test_zero_post_orders_negative_lift(self):
        posts = [{"id": 1, "title": "T", "publish_date": "2026-05-15",
                  "read_user_count": 50, "share_user_count": 0}]
        daily = {"2026-05-08": _daily(5)}  # pre has orders, post has none
        result = compute_content_impact(posts, daily, window_days=7)
        r = result[0]
        assert r["post_orders"] == 0
        assert r["order_lift_pct"] == pytest.approx(-100.0)

    def test_result_contains_all_expected_fields(self):
        posts = [{"id": 42, "title": "T", "publish_date": "2026-05-15",
                  "read_user_count": 123, "share_user_count": 7}]
        result = compute_content_impact(posts, {}, window_days=7)
        r = result[0]
        assert r["post_id"] == 42
        assert r["title"] == "T"
        assert r["publish_date"] == "2026-05-15"
        assert r["read_user_count"] == 123
        assert "pre_orders" in r
        assert "post_orders" in r
        assert "pre_revenue" in r
        assert "post_revenue" in r
        assert "order_lift_pct" in r
        assert "revenue_lift_pct" in r


# ─────────────────────────────────────────────────────────────────────────────
# API integration tests
# ─────────────────────────────────────────────────────────────────────────────

_YOUZAN_HEADER = "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额"


def _order_csv(rows: list[tuple]) -> bytes:
    """rows: [(order_id, date_str, phone, sku, qty, price), ...]"""
    lines = [_YOUZAN_HEADER]
    for r in rows:
        lines.append(",".join(str(x) for x in r))
    return "\n".join(lines).encode()


def _upload_orders(client, tokens, rows: list[tuple]):
    r = client.post(
        "/upload/",
        files={"file": ("orders.csv", _order_csv(rows))},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 202
    # poll for completion
    batch_id = r.json()["batch_id"]
    import time
    for _ in range(10):
        rb = client.get(f"/upload/batches/{batch_id}", headers=_auth(tokens["admin"]))
        if rb.json().get("status") in ("done", "failed"):
            break
        time.sleep(0.1)


def _sync_posts(client, tokens, monkeypatch, articles):
    for k in ["WECHAT_OFFICIAL_APP_ID", "WECHAT_OFFICIAL_APP_SECRET",
              "WECHAT_OFFICIAL_ACCOUNT_NAME"]:
        monkeypatch.delenv(k, raising=False)
    for i in range(1, 11):
        for s in ["APP_ID", "APP_SECRET", "ACCOUNT_NAME"]:
            monkeypatch.delenv(f"WECHAT_{s}_{i}", raising=False)

    monkeypatch.setenv("WECHAT_OFFICIAL_APP_ID", "wx-ci-test")
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_SECRET", "secret-ci")
    monkeypatch.setenv("WECHAT_OFFICIAL_ACCOUNT_NAME", "CI Account")

    import app.views.media.routes as media_view

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def fetch_article_total_rows(self, *a, **kw):
            return articles

    monkeypatch.setattr(media_view, "WeChatOfficialClient", FakeClient)

    r = client.post(
        "/media/wechat/sync",
        json={"start_date": "2026-05-01", "end_date": "2026-05-31"},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 200


def test_content_impact_returns_impact_for_each_post(client, tokens, monkeypatch):
    # Article published 2026-05-15; pre-window 5/8–5/14, post-window 5/15–5/21
    _sync_posts(client, tokens, monkeypatch, [
        {
            "external_id": "ci-1", "title": "示例商品新品发布", "publish_date": date(2026, 5, 15),
            "metric_date": date(2026, 5, 15), "url": None, "read_user_count": 800,
            "share_user_count": 30, "like_user": 5, "comment_count": 0, "collection_user": 2,
            "read_avg_time": 1.2, "read_user_source": None, "read_finish_rate": None, "raw_payload": {},
        }
    ])
    _upload_orders(client, tokens, [
        ("9001", "2026-05-08", "13800000001", "示例商品", 1, 299),
        ("9002", "2026-05-10", "13800000002", "示例商品", 1, 299),
        ("9003", "2026-05-15", "13800000003", "示例商品", 1, 299),
        ("9004", "2026-05-16", "13800000004", "示例商品", 1, 299),
        ("9005", "2026-05-17", "13800000005", "示例商品", 1, 299),
        ("9006", "2026-05-18", "13800000006", "示例商品", 1, 299),
    ])

    r = client.get(
        "/media/content-impact",
        params={"start_date": "2026-05-10", "end_date": "2026-05-20", "window_days": 7},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    row = next(x for x in data if x["title"] == "示例商品新品发布")
    assert row["pre_orders"] == 2   # 5/8 and 5/10 fall in pre-window
    assert row["post_orders"] == 4  # 5/15, 5/16, 5/17, 5/18
    assert row["order_lift_pct"] == pytest.approx(100.0)


def test_content_impact_lift_is_none_when_no_orders(client, tokens, monkeypatch):
    _sync_posts(client, tokens, monkeypatch, [
        {
            "external_id": "ci-noop", "title": "No orders article", "publish_date": date(2026, 5, 15),
            "metric_date": date(2026, 5, 15), "url": None, "read_user_count": 100,
            "share_user_count": 2, "like_user": 0, "comment_count": 0, "collection_user": 0,
            "read_avg_time": None, "read_user_source": None, "read_finish_rate": None, "raw_payload": {},
        }
    ])

    r = client.get(
        "/media/content-impact",
        params={"start_date": "2026-05-15", "end_date": "2026-05-15", "window_days": 7},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    row = next(x for x in data if x["title"] == "No orders article")
    assert row["pre_orders"] == 0
    assert row["post_orders"] == 0
    assert row["order_lift_pct"] is None


def test_content_impact_window_days_affects_results(client, tokens, monkeypatch):
    _sync_posts(client, tokens, monkeypatch, [
        {
            "external_id": "ci-win", "title": "Window test article", "publish_date": date(2026, 5, 15),
            "metric_date": date(2026, 5, 15), "url": None, "read_user_count": 200,
            "share_user_count": 5, "like_user": 0, "comment_count": 0, "collection_user": 0,
            "read_avg_time": None, "read_user_source": None, "read_finish_rate": None, "raw_payload": {},
        }
    ])
    _upload_orders(client, tokens, [
        ("w001", "2026-05-05", "13800000001", "item", 1, 100),  # 10 days before — outside window=7
        ("w002", "2026-05-14", "13800000002", "item", 1, 100),  # 1 day before — inside window=7
        ("w003", "2026-05-15", "13800000003", "item", 1, 100),  # publish day
    ])

    r7 = client.get(
        "/media/content-impact",
        params={"start_date": "2026-05-15", "end_date": "2026-05-15", "window_days": 7},
        headers=_auth(tokens["analyst"]),
    )
    r14 = client.get(
        "/media/content-impact",
        params={"start_date": "2026-05-15", "end_date": "2026-05-15", "window_days": 14},
        headers=_auth(tokens["analyst"]),
    )
    assert r7.status_code == 200
    assert r14.status_code == 200

    row7 = next(x for x in r7.json() if x["title"] == "Window test article")
    row14 = next(x for x in r14.json() if x["title"] == "Window test article")

    # window=7: pre has 5/14 only (1 order); window=14: pre has 5/5 AND 5/14 (2 orders)
    assert row7["pre_orders"] == 1
    assert row14["pre_orders"] == 2


def test_content_impact_returns_empty_when_no_posts(client, tokens):
    r = client.get(
        "/media/content-impact",
        params={"start_date": "2020-01-01", "end_date": "2020-01-31"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    assert r.json() == []


def test_content_impact_forbidden_for_viewer(client, tokens):
    r = client.get("/media/content-impact", headers=_auth(tokens["viewer"]))
    assert r.status_code == 403


def test_content_impact_requires_auth(client):
    r = client.get("/media/content-impact")
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# source= param — XHS / Zhihu / default-regression
# ─────────────────────────────────────────────────────────────────────────────

from tests.test_xhs import account, _make_xhs_xlsx_bytes, _one_row as _one_xhs_row  # noqa: F401
from tests.test_zhihu import _make_article_csv_bytes, _one_article


def _upload_xhs_post(client, tokens, account, title, publish_date_cn, views=500, shares=20):
    row = _one_xhs_row(**{
        "笔记标题": title,
        "首次发布时间": publish_date_cn,
        "观看量": str(views),
        "分享": str(shares),
    })
    r = client.post(
        "/media/xhs/upload",
        data={"account_id": account},
        files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes([row]), "application/octet-stream")},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 200, r.text


def _upload_zhihu_article(client, tokens, title, publish_date_iso, reads=500, shares=20):
    row = _one_article(**{
        "标题": title,
        "发布时间": publish_date_iso,
        "阅读": str(reads),
        "分享": str(shares),
    })
    r = client.post(
        "/media/zhihu/upload",
        data={"content_type": "article"},
        files={"file": ("zh.csv", _make_article_csv_bytes([row]), "application/octet-stream")},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 200, r.text


class TestContentImpactXhsSource:
    def test_source_xhs_uses_xhs_posts(self, client, tokens, account):
        _upload_xhs_post(client, tokens, account, "示例商品笔记测试",
                          "2026年06月15日12时00分00秒")
        _upload_orders(client, tokens, [
            ("xhs-9001", "2026-06-08", "13800001001", "示例商品", 1, 199),
            ("xhs-9002", "2026-06-15", "13800001002", "示例商品", 1, 199),
            ("xhs-9003", "2026-06-16", "13800001003", "示例商品", 1, 199),
        ])

        r = client.get(
            "/media/content-impact",
            params={"start_date": "2026-06-10", "end_date": "2026-06-20",
                    "window_days": 7, "source": "xhs"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        row = next(x for x in data if x["title"] == "示例商品笔记测试")
        assert row["pre_orders"] == 1   # 6/8
        assert row["post_orders"] == 2  # 6/15, 6/16


class TestContentImpactZhihuSource:
    def test_source_zhihu_uses_zhihu_posts(self, client, tokens):
        _upload_zhihu_article(client, tokens, "知乎示例科普文章", "2026-06-15")
        _upload_orders(client, tokens, [
            ("zh-9001", "2026-06-08", "13800002001", "示例商品", 1, 199),
            ("zh-9002", "2026-06-15", "13800002002", "示例商品", 1, 199),
            ("zh-9003", "2026-06-16", "13800002003", "示例商品", 1, 199),
        ])

        r = client.get(
            "/media/content-impact",
            params={"start_date": "2026-06-10", "end_date": "2026-06-20",
                    "window_days": 7, "source": "zhihu"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        row = next(x for x in data if x["title"] == "知乎示例科普文章")
        assert row["pre_orders"] == 1
        assert row["post_orders"] == 2


class TestContentImpactSourceDefaultIsWechatRegression:
    def test_omitting_source_param_preserves_existing_behavior(self, client, tokens, monkeypatch):
        _sync_posts(client, tokens, monkeypatch, [
            {
                "external_id": "ci-default", "title": "默认来源回归测试", "publish_date": date(2026, 5, 15),
                "metric_date": date(2026, 5, 15), "url": None, "read_user_count": 400,
                "share_user_count": 15, "like_user": 0, "comment_count": 0, "collection_user": 0,
                "read_avg_time": None, "read_user_source": None, "read_finish_rate": None, "raw_payload": {},
            }
        ])
        _upload_orders(client, tokens, [
            ("d001", "2026-05-08", "13800009001", "item", 1, 100),
            ("d002", "2026-05-15", "13800009002", "item", 1, 100),
        ])

        r_omitted = client.get(
            "/media/content-impact",
            params={"start_date": "2026-05-15", "end_date": "2026-05-15", "window_days": 7},
            headers=_auth(tokens["analyst"]),
        )
        r_explicit = client.get(
            "/media/content-impact",
            params={"start_date": "2026-05-15", "end_date": "2026-05-15", "window_days": 7, "source": "wechat"},
            headers=_auth(tokens["analyst"]),
        )
        assert r_omitted.status_code == 200
        assert r_explicit.status_code == 200
        assert r_omitted.json() == r_explicit.json()

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.wechat_official import (
    WeChatAPIError,
    WeChatOfficialClient,
    iter_dates,
    normalize_article_total_detail_item,
)

SOURCE_BREAKDOWN = [
    {"user_count": 17, "scene_desc": "朋友圈"},
    {"user_count": 2, "scene_desc": "公众号主页"},
    {"user_count": 19, "scene_desc": "全部"},
]


def test_iter_dates_inclusive():
    assert list(iter_dates(date(2026, 5, 20), date(2026, 5, 22))) == [
        date(2026, 5, 20),
        date(2026, 5, 21),
        date(2026, 5, 22),
    ]


def test_iter_dates_rejects_reversed_range():
    with pytest.raises(ValueError):
        list(iter_dates(date(2026, 5, 22), date(2026, 5, 20)))


def test_normalize_article_total_detail_item_maps_wechat_shape():
    detail = {
        "stat_date": "2026-05-21",
        "read_user": 90,
        "share_user": 5,
        "like_user": 3,
        "comment_count": 1,
        "collection_user": 2,
        "read_avg_activetime": 0.12,
        "read_user_source": SOURCE_BREAKDOWN,
        "zaikan_user": 0,
    }
    item = {
        "ref_date": "2026-05-21",
        "msgid": "100000001_1",
        "title": "How we use product",
        "content_url": "http://mp.weixin.qq.com/s?__biz=xxx",
        "publish_type": 1,
        "detail_list": [detail],
    }

    rows = normalize_article_total_detail_item(item)

    assert len(rows) == 1
    row = rows[0]
    assert row["external_id"] == "100000001_1"
    assert row["title"] == "How we use product"
    assert row["publish_date"] == date(2026, 5, 21)
    assert row["metric_date"] == date(2026, 5, 21)
    assert row["url"] == "http://mp.weixin.qq.com/s?__biz=xxx"
    assert row["read_user_count"] == 90
    assert row["share_user_count"] == 5
    assert row["like_user"] == 3
    assert row["comment_count"] == 1
    assert row["collection_user"] == 2
    assert row["read_avg_time"] == 0.12
    assert row["read_user_source"] == SOURCE_BREAKDOWN
    assert row["raw_payload"] is detail


def test_normalize_article_total_detail_item_multi_day():
    item = {
        "ref_date": "2026-05-20",
        "msgid": "100000002_1",
        "title": "Multi-day article",
        "content_url": None,
        "detail_list": [
            {"stat_date": "2026-05-20", "read_user": 10, "share_user": 1, "like_user": 0,
             "comment_count": 0, "collection_user": 0, "read_avg_activetime": 0.1, "read_user_source": []},
            {"stat_date": "2026-05-21", "read_user": 25, "share_user": 3, "like_user": 2,
             "comment_count": 1, "collection_user": 1, "read_avg_activetime": 0.15, "read_user_source": []},
        ],
    }

    rows = normalize_article_total_detail_item(item)

    assert len(rows) == 2
    assert rows[0]["metric_date"] == date(2026, 5, 20)
    assert rows[1]["metric_date"] == date(2026, 5, 21)
    assert rows[1]["read_user_count"] == 25


def test_normalize_article_total_detail_item_skips_missing_stat_date():
    item = {
        "ref_date": "2026-05-21",
        "msgid": "baditem",
        "title": "Article",
        "content_url": None,
        "detail_list": [{"read_user": 5}],  # no stat_date
    }
    rows = normalize_article_total_detail_item(item)
    assert rows == []


def test_normalize_article_total_detail_item_empty_detail_list():
    item = {"ref_date": "2026-05-21", "msgid": "empty", "title": "Empty", "content_url": None, "detail_list": []}
    assert normalize_article_total_detail_item(item) == []


class TestFetchPublishedArticleDates:
    """Unit tests for WeChatOfficialClient.fetch_published_article_dates."""

    def _make_client(self) -> WeChatOfficialClient:
        return WeChatOfficialClient(app_id="fake_id", app_secret="fake_secret")

    def _ts(self, d: date) -> int:
        """Convert a date to a UTC Unix timestamp (noon, to avoid DST edge cases)."""
        return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())

    def _mock_token(self, client: WeChatOfficialClient, token: str = "tok") -> None:
        client.get_access_token = MagicMock(return_value=token)

    def test_returns_dates_within_window(self):
        client = self._make_client()
        self._mock_token(client)
        today = date.today()
        recent = today - timedelta(days=10)
        old = today - timedelta(days=200)  # outside 170-day window

        page = {
            "total_count": 2,
            "item": [
                {"update_time": self._ts(recent)},
                {"update_time": self._ts(old)},
            ],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = page

        with patch("app.connectors.wechat_official.requests.post", return_value=mock_resp):
            result = client.fetch_published_article_dates(window_days=170)

        assert recent in result
        assert old not in result

    def test_excludes_articles_older_than_window(self):
        client = self._make_client()
        self._mock_token(client)
        today = date.today()
        cutoff_minus_one = today - timedelta(days=171)

        page = {
            "total_count": 1,
            "item": [{"update_time": self._ts(cutoff_minus_one)}],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = page

        with patch("app.connectors.wechat_official.requests.post", return_value=mock_resp):
            result = client.fetch_published_article_dates(window_days=170)

        assert result == []

    def test_paginates_until_total_exhausted(self):
        client = self._make_client()
        self._mock_token(client)
        today = date.today()
        d1 = today - timedelta(days=5)
        d2 = today - timedelta(days=10)

        page1 = {"total_count": 2, "item": [{"update_time": self._ts(d1)}]}
        page2 = {"total_count": 2, "item": [{"update_time": self._ts(d2)}]}
        mock_resp1, mock_resp2 = MagicMock(), MagicMock()
        mock_resp1.json.return_value = page1
        mock_resp2.json.return_value = page2

        with patch(
            "app.connectors.wechat_official.requests.post",
            side_effect=[mock_resp1, mock_resp2],
        ):
            result = client.fetch_published_article_dates(window_days=170)

        assert sorted(result) == sorted([d1, d2])

    def test_returns_sorted_ascending(self):
        client = self._make_client()
        self._mock_token(client)
        today = date.today()
        dates_unordered = [today - timedelta(days=n) for n in [30, 5, 90, 15]]

        page = {
            "total_count": len(dates_unordered),
            "item": [{"update_time": self._ts(d)} for d in dates_unordered],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = page

        with patch("app.connectors.wechat_official.requests.post", return_value=mock_resp):
            result = client.fetch_published_article_dates(window_days=170)

        assert result == sorted(result)

    def test_raises_on_api_error(self):
        client = self._make_client()
        self._mock_token(client)

        error_resp = MagicMock()
        error_resp.json.return_value = {"errcode": 40001, "errmsg": "invalid credential"}

        with patch("app.connectors.wechat_official.requests.post", return_value=error_resp):
            with pytest.raises(WeChatAPIError):
                client.fetch_published_article_dates()

    def test_empty_account_returns_empty_list(self):
        client = self._make_client()
        self._mock_token(client)

        empty_page = {"total_count": 0, "item": []}
        mock_resp = MagicMock()
        mock_resp.json.return_value = empty_page

        with patch("app.connectors.wechat_official.requests.post", return_value=mock_resp):
            result = client.fetch_published_article_dates()

        assert result == []


def test_wechat_api_error_includes_payload_message():
    exc = WeChatAPIError({"errcode": 40013, "errmsg": "invalid appid"})
    assert "40013" in str(exc)
    assert "invalid appid" in str(exc)


JUMP_POSITION = [
    {"position": "0%", "user_count": 10},
    {"position": "25%", "user_count": 8},
]


def _full_detail(**overrides) -> dict:
    """Return a detail_list row with all known fields."""
    base = {
        "stat_date": "2026-05-21",
        "read_user": 90,
        "share_user": 5,
        "like_user": 3,
        "comment_count": 1,
        "collection_user": 2,
        "read_avg_activetime": 0.12,
        "read_user_source": SOURCE_BREAKDOWN,
        "zaikan_user": 7,
        "read_subscribe_user": 12,
        "read_delivery_rate": 0.85,
        "praise_money": 3,
        "read_jump_position": JUMP_POSITION,
        "read_finish_rate": 0.45,
    }
    base.update(overrides)
    return base


def _full_item(**overrides) -> dict:
    """Return an article item with all known top-level fields."""
    base = {
        "ref_date": "2026-05-21",
        "msgid": "100000001_1",
        "title": "Full field test",
        "content_url": "http://mp.weixin.qq.com/s?__biz=xxx",
        "publish_type": 1,
        "detail_list": [_full_detail()],
    }
    base.update(overrides)
    return base


class TestNormalizeMissingFields:
    def _row(self):
        rows = normalize_article_total_detail_item(_full_item())
        assert len(rows) == 1
        return rows[0]

    def test_zaikan_user_captured(self):
        assert self._row()["zaikan_user"] == 7

    def test_read_subscribe_user_captured(self):
        assert self._row()["read_subscribe_user"] == 12

    def test_read_delivery_rate_captured(self):
        assert self._row()["read_delivery_rate"] == pytest.approx(0.85)

    def test_praise_money_captured(self):
        assert self._row()["praise_money"] == 3

    def test_read_jump_position_captured(self):
        assert self._row()["read_jump_position"] == JUMP_POSITION

    def test_read_finish_rate_captured(self):
        assert self._row()["read_finish_rate"] == pytest.approx(0.45)

    def test_publish_type_captured(self):
        assert self._row()["publish_type"] == 1

    def test_missing_optional_fields_default_to_none(self):
        detail = {
            "stat_date": "2026-05-21",
            "read_user": 10,
            "share_user": 0,
            "like_user": 0,
            "comment_count": 0,
            "collection_user": 0,
            "read_avg_activetime": 0.0,
            "read_user_source": [],
        }
        item = {
            "ref_date": "2026-05-21",
            "msgid": "min_item",
            "title": "Minimal",
            "content_url": None,
            "detail_list": [detail],
        }
        rows = normalize_article_total_detail_item(item)
        assert len(rows) == 1
        row = rows[0]
        assert row["zaikan_user"] == 0
        assert row["read_subscribe_user"] == 0
        assert row["read_delivery_rate"] is None
        assert row["praise_money"] == 0
        assert row["read_jump_position"] is None
        assert row["read_finish_rate"] is None
        assert row["publish_type"] is None

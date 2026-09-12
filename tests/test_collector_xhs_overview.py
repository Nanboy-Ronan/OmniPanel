"""Tests for app/collector/xhs.py::collect_xhs_overview() and its JSON
parsing helpers.

Two layers, mirroring test_collector_xhs.py's approach:
  1. Parsing helpers (_local_date_from_ms, _build_daily_rows,
     _parse_audience_source) run against real captured API responses
     (data/xhs_account_base_example.json, data/xhs_audience_source_example.json)
     — no mocking, these are the actual shapes XHS returns.
  2. collect_xhs_overview() itself, with open_context() monkeypatched to a
     fake page whose .request.get() stands in for the real authenticated
     GETs, so the auth/error-classification logic is exercised without a
     live browser.
"""
from __future__ import annotations

import contextlib
import json
from datetime import date
from pathlib import Path

import pytest

from app.collector import xhs
from app.collector.errors import SessionExpiredError, WrongAccountError

_DATA_DIR = Path(__file__).parent.parent / "data"
_BASE_FIXTURE = _DATA_DIR / "xhs_account_base_example.json"
_SOURCE_FIXTURE = _DATA_DIR / "xhs_audience_source_example.json"
# Sample files are git-ignored (see .gitignore's `data/` rule — no real
# exports checked in). Same convention as tests/test_xhs.py /
# tests/test_zhihu.py's "real example file" smoke tests: skip rather than
# fail when they're not present locally.
_MISSING_SAMPLE = "sample file not present (data/ is git-ignored; drop a synthetic-content copy there locally to run this test)"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestLocalDateFromMs:
    def test_matches_confirmed_live_value(self):
        # Confirmed live 2026-09-11 against account/base's own begin_time —
        # this ms value is Asia/Shanghai midnight of 2026-09-05, not the
        # UTC calendar date (2026-09-04) a naive conversion would give.
        assert xhs._local_date_from_ms(1788537600000) == date(2026, 9, 5)


class TestShanghaiToday:
    def test_utc_evening_run_is_next_calendar_day_in_shanghai(self, monkeypatch):
        # The daily collector actually runs at 6:38 Beijing == 22:38 UTC the
        # *previous* day (see docs/collector.md). dt.date.today() on the
        # VM (server-local/UTC clock) would silently report the wrong,
        # earlier calendar date for snapshot_date at exactly this moment —
        # this is the failure this helper exists to avoid.
        import datetime as _dt

        class _FixedDatetime(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                fixed_utc = _dt.datetime(2026, 9, 10, 22, 38, tzinfo=_dt.timezone.utc)
                return fixed_utc.astimezone(tz) if tz else fixed_utc

        monkeypatch.setattr(xhs, "datetime", _FixedDatetime)
        assert xhs._shanghai_today() == date(2026, 9, 11)


class TestBuildDailyRows:
    """Against the real captured account/base response (data/xhs_account_base_example.json)."""

    @pytest.fixture
    def thirty(self):
        if not _BASE_FIXTURE.exists():
            pytest.skip(_MISSING_SAMPLE)
        body = _load(_BASE_FIXTURE)
        return body["data"]["thirty"]

    def test_produces_one_row_per_calendar_date(self, thirty):
        rows = xhs._build_daily_rows(thirty)
        dates = [r["metric_date"] for r in rows]
        assert dates == sorted(dates)
        assert len(dates) == len(set(dates))
        assert len(rows) == 30

    def test_count_fields_come_from_plain_count(self, thirty):
        rows = xhs._build_daily_rows(thirty)
        # rise_fans_list sums to the window's own rise_fans_count (verified
        # live 2026-09-11 — these are genuine per-day deltas, not a running
        # total split across days).
        assert sum(r["rise_fans_count"] for r in rows) == thirty["rise_fans_count"]
        assert sum(r["loss_fans_count"] for r in rows) == thirty["loss_fans_count"]
        assert sum(r["net_rise_fans_count"] for r in rows) == thirty["net_rise_fans_count"]
        assert sum(r["view_count"] for r in rows) == thirty["view_count"]
        assert sum(r["like_count"] for r in rows) == thirty["like_count"]

    def test_rate_fields_use_the_precise_double_not_the_rounded_int(self, thirty):
        # video_full_view_rate_list carries both a rounded `count` and a
        # precise `count_with_double` (e.g. 16 vs 16.5) — the row must carry
        # the double, not silently truncate a completion rate.
        rows = xhs._build_daily_rows(thirty)
        by_date = {r["metric_date"]: r for r in rows}
        for entry in thirty["cover_click_rate_list"]:
            d = xhs._local_date_from_ms(entry["date"]).isoformat()
            assert by_date[d]["cover_click_rate"] == entry["count_with_double"]
        for entry in thirty["video_full_view_rate_list"]:
            d = xhs._local_date_from_ms(entry["date"]).isoformat()
            assert by_date[d]["video_full_view_rate"] == entry["count_with_double"]


class TestParseAudienceSource:
    @pytest.fixture
    def data(self):
        if not _SOURCE_FIXTURE.exists():
            pytest.skip(_MISSING_SAMPLE)
        return _load(_SOURCE_FIXTURE)["data"]

    def test_returns_both_windows(self, data):
        rows = xhs._parse_audience_source(data)
        windows = {r["window"] for r in rows}
        assert windows == {"seven", "thirty"}

    def test_each_window_shares_sum_to_roughly_100_percent(self, data):
        rows = xhs._parse_audience_source(data)
        for window in ("seven", "thirty"):
            total = sum(r["value_pct"] for r in rows if r["window"] == window)
            assert total == pytest.approx(100, abs=1)

    def test_titles_and_source_types_pass_through(self, data):
        rows = xhs._parse_audience_source(data)
        seven = [r for r in rows if r["window"] == "seven"]
        titles = {r["source_type"]: r["title"] for r in seven}
        assert titles.get(1) == "首页推荐"
        assert titles.get(2) == "搜索"


# ── collect_xhs_overview() end-to-end against a fake page ──────────────────
#
# collect_xhs_overview() captures the SPA's own account/base + audience/
# source requests passively via page.on("response") rather than issuing
# page.request.get() itself — confirmed live 2026-09-11 that the latter
# gets rejected with HTTP 406 (see the function's docstring). So the fake
# page here models a page.on("response") listener and "fires" configured
# responses once goto() is called, mirroring how the SPA's own requests
# land shortly after navigation — not a fake request/response client.

class _FakeResponse:
    def __init__(self, url: str, body=None, raise_on_json: bool = False):
        self.url = url
        self._body = body or {}
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("response body is not JSON")
        return self._body


class _FakePage:
    """Same shape as test_collector_xhs.py's _FakePage, plus .on("response",
    ...) registration and a set of responses that "arrive" (are handed to
    every registered handler) the first time goto() is called."""

    def __init__(self, url_sequence, body_text="", responses_to_fire=None):
        self._sequence = url_sequence
        self._idx = 0
        self.url = url_sequence[0]
        self.body_text = body_text
        self._responses_to_fire = responses_to_fire or []
        self._response_handlers: list = []
        self._fired = False

    def on(self, event, handler):
        if event == "response":
            self._response_handlers.append(handler)

    def goto(self, url, wait_until=None):
        self._idx = 0
        self.url = self._sequence[0]
        if not self._fired:
            self._fired = True
            for resp in self._responses_to_fire:
                for handler in self._response_handlers:
                    handler(resp)

    def wait_for_timeout(self, ms):
        self._idx += 1
        self.url = self._sequence[min(self._idx, len(self._sequence) - 1)]

    def inner_text(self, selector):
        return self.body_text


def _fake_open_context_factory(page):
    @contextlib.contextmanager
    def _fake_open_context(storage_path, headless=None):
        yield page
    return _fake_open_context


_OK_BASE_RESPONSE = _FakeResponse(
    "", body={"code": 0, "success": True, "data": {"thirty": {}}},
)
_OK_SOURCE_RESPONSE = _FakeResponse(
    "", body={"code": 0, "success": True, "data": {"seven": [], "thirty": []}},
)


def _with_url(resp: _FakeResponse, url: str) -> _FakeResponse:
    resp.url = url
    return resp


class TestCollectXhsOverview:
    def test_session_expired_before_any_api_call(self, tmp_path, monkeypatch):
        page = _FakePage(["https://pro.xiaohongshu.com/login"])
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        with pytest.raises(SessionExpiredError):
            xhs.collect_xhs_overview(tmp_path / "session.json")

    def test_wrong_account_raises_before_any_api_call(self, tmp_path, monkeypatch):
        page = _FakePage(
            ["https://pro.xiaohongshu.com/enterprise/home"],
            body_text="没有查看当前页面的权限，请联系管理员",
        )
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        with pytest.raises(WrongAccountError):
            xhs.collect_xhs_overview(tmp_path / "session.json")

    def test_nonzero_api_code_raises_xhs_api_error(self, tmp_path, monkeypatch):
        page = _FakePage(
            ["https://creator.xiaohongshu.com/statistics/account/v2"],
            responses_to_fire=[
                _with_url(
                    _FakeResponse("", body={"code": 1, "msg": "无权限", "success": False}),
                    xhs.XHS_ACCOUNT_BASE_API,
                ),
                _with_url(_OK_SOURCE_RESPONSE, xhs.XHS_AUDIENCE_SOURCE_API),
            ],
        )
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        with pytest.raises(xhs.XhsApiError):
            xhs.collect_xhs_overview(tmp_path / "session.json")

    def test_response_never_observed_within_budget_raises_xhs_api_error(self, tmp_path, monkeypatch):
        # Only account/base's request ever "fires" — audience/source never
        # lands (SPA didn't call it, or it never returned parseable JSON).
        # _API_CAPTURE_MAX_MS/_API_CAPTURE_POLL_MS are monkeypatched down so
        # the fake's wait_for_timeout loop (no real sleeping) still exercises
        # the real budget-exceeded code path quickly.
        monkeypatch.setattr(xhs, "_API_CAPTURE_MAX_MS", 1000)
        monkeypatch.setattr(xhs, "_API_CAPTURE_POLL_MS", 500)
        page = _FakePage(
            ["https://creator.xiaohongshu.com/statistics/account/v2"],
            responses_to_fire=[_with_url(_OK_BASE_RESPONSE, xhs.XHS_ACCOUNT_BASE_API)],
        )
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        with pytest.raises(xhs.XhsApiError, match="audience/source"):
            xhs.collect_xhs_overview(tmp_path / "session.json")

    def test_non_json_response_is_treated_as_not_observed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(xhs, "_API_CAPTURE_MAX_MS", 1000)
        monkeypatch.setattr(xhs, "_API_CAPTURE_POLL_MS", 500)
        page = _FakePage(
            ["https://creator.xiaohongshu.com/statistics/account/v2"],
            responses_to_fire=[
                _with_url(_FakeResponse("", raise_on_json=True), xhs.XHS_ACCOUNT_BASE_API),
                _with_url(_OK_SOURCE_RESPONSE, xhs.XHS_AUDIENCE_SOURCE_API),
            ],
        )
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        with pytest.raises(xhs.XhsApiError):
            xhs.collect_xhs_overview(tmp_path / "session.json")

    def test_success_returns_json_bytes_with_expected_shape(self, tmp_path, monkeypatch):
        if not (_BASE_FIXTURE.exists() and _SOURCE_FIXTURE.exists()):
            pytest.skip(_MISSING_SAMPLE)
        base_body = _load(_BASE_FIXTURE)
        source_body = _load(_SOURCE_FIXTURE)
        page = _FakePage(
            ["https://creator.xiaohongshu.com/statistics/account/v2"],
            responses_to_fire=[
                _with_url(_FakeResponse("", body=base_body), xhs.XHS_ACCOUNT_BASE_API),
                _with_url(_FakeResponse("", body=source_body), xhs.XHS_AUDIENCE_SOURCE_API),
            ],
        )
        monkeypatch.setattr(xhs, "open_context", _fake_open_context_factory(page))

        data, filename = xhs.collect_xhs_overview(tmp_path / "session.json")

        assert filename.startswith("xhs_overview_") and filename.endswith(".json")
        payload = json.loads(data.decode("utf-8"))
        assert len(payload["daily"]) == 30
        assert len(payload["audience_source"]) > 0
        assert payload["daily"][0]["metric_date"] < payload["daily"][-1]["metric_date"]
        # snapshot_date is embedded in the payload itself (Shanghai-local,
        # computed by the collector) rather than left for the upload
        # endpoint to default at whatever time it happens to process the
        # request — and the filename's date stamp must be the same value.
        assert payload["snapshot_date"] in filename
        date.fromisoformat(payload["snapshot_date"])  # raises if malformed

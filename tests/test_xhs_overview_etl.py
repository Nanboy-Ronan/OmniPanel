"""Tests for app/db/etl/xhs_overview.py against a real test DB.

Same SyncSessionLocal-monkeypatch pattern as test_collector_runs.py.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.db.etl.xhs_overview import (
    parse_xhs_overview_payload,
    upsert_xhs_audience_source,
    upsert_xhs_daily_metrics,
)


@pytest.fixture
def sync_db(pg_sync_url, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    import app.db as db

    engine = create_engine(pg_sync_url, future=True, echo=False)
    SyncSL = sessionmaker(engine, class_=Session, expire_on_commit=False)
    monkeypatch.setattr(db, "SyncSessionLocal", SyncSL, raising=False)
    yield SyncSL
    engine.dispose()


@pytest.fixture
def account_id(sync_db):
    """A shared 'overview-etl-test-account' XhsAccount id (create if absent).

    xhs_accounts is not in conftest's _clean_db truncate list (same as
    XhsPost's own tests), so tests within a session share one account row
    rather than colliding on its unique name constraint.
    """
    from sqlalchemy.exc import IntegrityError
    from app.db.models import XhsAccount

    with sync_db() as s:
        try:
            acc = XhsAccount(name="overview-etl-test-account")
            s.add(acc)
            s.commit()
            s.refresh(acc)
            return acc.id
        except IntegrityError:
            s.rollback()
            existing = s.query(XhsAccount).filter_by(name="overview-etl-test-account").one()
            return existing.id


# ─── parse_xhs_overview_payload (pure) ─────────────────────────────────────

class TestParseXhsOverviewPayload:
    def test_valid_daily_and_source_rows_pass_through(self):
        payload = {
            "daily": [{"metric_date": "2026-09-01", "rise_fans_count": 3, "view_count": 100}],
            "audience_source": [{"window": "seven", "source_type": 1, "title": "首页推荐", "value_pct": 65.0}],
            "snapshot_date": "2026-09-11",
        }
        daily, source, snapshot_date = parse_xhs_overview_payload(payload)
        assert snapshot_date == dt.date(2026, 9, 11)
        assert daily == [{
            "metric_date": dt.date(2026, 9, 1),
            "rise_fans_count": 3, "loss_fans_count": None, "net_rise_fans_count": None,
            "view_count": 100, "view_time_total_seconds": None, "avg_view_time_seconds": None,
            "home_view_count": None, "like_count": None, "collect_count": None,
            "comment_count": None, "share_count": None, "danmaku_count": None,
            "cover_click_rate": None, "video_full_view_rate": None,
        }]
        # Output key is window_label (the DB/ORM column name — `window` is a
        # Postgres reserved word), even though the wire-format input key
        # above is "window".
        assert source == [{"window_label": "seven", "source_type": 1, "title": "首页推荐", "value_pct": 65.0}]

    def test_row_with_unparseable_date_is_dropped_not_fatal(self):
        payload = {"daily": [
            {"metric_date": "not-a-date", "view_count": 1},
            {"metric_date": "2026-09-02", "view_count": 2},
        ]}
        daily, _, _ = parse_xhs_overview_payload(payload)
        assert len(daily) == 1
        assert daily[0]["metric_date"] == dt.date(2026, 9, 2)

    def test_source_row_with_unknown_window_is_dropped(self):
        payload = {"audience_source": [
            {"window": "ninety", "source_type": 1, "title": "x", "value_pct": 1},
            {"window": "seven", "source_type": 2, "title": "搜索", "value_pct": 18},
        ]}
        _, source, _ = parse_xhs_overview_payload(payload)
        assert len(source) == 1
        assert source[0]["window_label"] == "seven"

    def test_empty_payload_returns_empty_lists_and_no_snapshot_date(self):
        assert parse_xhs_overview_payload({}) == ([], [], None)

    def test_missing_snapshot_date_falls_back_to_none_not_raise(self):
        _, _, snapshot_date = parse_xhs_overview_payload({"daily": [], "audience_source": []})
        assert snapshot_date is None


# ─── upsert_xhs_daily_metrics ───────────────────────────────────────────────

class TestUpsertXhsDailyMetrics:
    def test_insert_then_reupsert_overwrites_same_row_not_duplicate(self, sync_db, account_id):
        from app.db.models import XhsAccountDailyMetric

        rows = [{"metric_date": dt.date(2026, 9, 1), "rise_fans_count": 3, "view_count": 100}]
        with sync_db() as s:
            written = upsert_xhs_daily_metrics(rows, account_id, s)
        assert written == 1

        # Re-collect the same day with an updated value (self-healing /
        # re-run scenario) — must overwrite, not insert a second row.
        rows2 = [{"metric_date": dt.date(2026, 9, 1), "rise_fans_count": 5, "view_count": 150}]
        with sync_db() as s:
            upsert_xhs_daily_metrics(rows2, account_id, s)

        with sync_db() as s:
            all_rows = s.query(XhsAccountDailyMetric).filter_by(
                account_id=account_id, metric_date=dt.date(2026, 9, 1)
            ).all()
        assert len(all_rows) == 1
        assert all_rows[0].rise_fans_count == 5
        assert all_rows[0].view_count == 150

    def test_multiple_days_produce_multiple_rows(self, sync_db, account_id):
        from app.db.models import XhsAccountDailyMetric

        rows = [
            {"metric_date": dt.date(2026, 9, d), "view_count": d}
            for d in (1, 2, 3)
        ]
        with sync_db() as s:
            written = upsert_xhs_daily_metrics(rows, account_id, s)
        assert written == 3

        with sync_db() as s:
            all_rows = s.query(XhsAccountDailyMetric).filter_by(account_id=account_id).all()
        assert {r.metric_date for r in all_rows} == {dt.date(2026, 9, d) for d in (1, 2, 3)}

    def test_empty_rows_is_a_noop(self, sync_db, account_id):
        with sync_db() as s:
            written = upsert_xhs_daily_metrics([], account_id, s)
        assert written == 0


# ─── upsert_xhs_audience_source ─────────────────────────────────────────────

class TestUpsertXhsAudienceSource:
    def test_insert_then_reupsert_same_day_overwrites(self, sync_db, account_id):
        from app.db.models import XhsAudienceSourceDaily

        snapshot_date = dt.date(2026, 9, 11)
        rows = [{"window_label": "seven", "source_type": 1, "title": "首页推荐", "value_pct": 65.0}]
        with sync_db() as s:
            upsert_xhs_audience_source(rows, account_id, s, snapshot_date=snapshot_date)

        rows2 = [{"window_label": "seven", "source_type": 1, "title": "首页推荐", "value_pct": 70.0}]
        with sync_db() as s:
            upsert_xhs_audience_source(rows2, account_id, s, snapshot_date=snapshot_date)

        with sync_db() as s:
            all_rows = s.query(XhsAudienceSourceDaily).filter_by(
                account_id=account_id, snapshot_date=snapshot_date
            ).all()
        assert len(all_rows) == 1
        assert all_rows[0].value_pct == 70.0

    def test_different_windows_are_separate_rows(self, sync_db, account_id):
        from app.db.models import XhsAudienceSourceDaily

        snapshot_date = dt.date(2026, 9, 11)
        rows = [
            {"window_label": "seven", "source_type": 1, "title": "首页推荐", "value_pct": 65.0},
            {"window_label": "thirty", "source_type": 1, "title": "首页推荐", "value_pct": 57.0},
        ]
        with sync_db() as s:
            written = upsert_xhs_audience_source(rows, account_id, s, snapshot_date=snapshot_date)
        assert written == 2

        with sync_db() as s:
            all_rows = s.query(XhsAudienceSourceDaily).filter_by(account_id=account_id).all()
        assert {r.window_label for r in all_rows} == {"seven", "thirty"}

    def test_empty_rows_is_a_noop(self, sync_db, account_id):
        with sync_db() as s:
            written = upsert_xhs_audience_source([], account_id, s)
        assert written == 0

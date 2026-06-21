"""Tests for cohort retention analysis (客户留存分析).

Additive feature — does not change any existing endpoint. Two layers:
  1. Pure-function tests for build_cohort_matrix (no DB).
  2. API tests for GET /analysis/cohort_retention against known histories.
"""
from __future__ import annotations

import pytest

from app.views.ecommerce.analysis import build_cohort_matrix
from app.utils.cache import analysis_cache

from test_api_endpoints import client, tokens  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
# Pure-function tests — build_cohort_matrix (no DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCohortMatrix:
    def test_per_period_zero_is_one_and_cumulative_zero_is_zero(self):
        result = build_cohort_matrix(
            cohort_sizes={"2025-01": 10},
            per_period_counts={("2025-01", 0): 10, ("2025-01", 1): 4},
            first_repeat_counts={("2025-01", 1): 4},
            latest_month_index=2025 * 12 + 6,
            max_offset=12,
        )
        cohort = result[0]
        assert cohort["per_period"][0] == 1.0
        assert cohort["cumulative"][0] == 0.0
        assert cohort["per_period"][1] == 0.4
        assert cohort["cumulative"][1] == 0.4

    def test_right_censoring_produces_trailing_nulls(self):
        # Cohort 2026-05, latest data month 2026-06 → only offsets 0 and 1 observable.
        result = build_cohort_matrix(
            cohort_sizes={"2026-05": 8},
            per_period_counts={("2026-05", 0): 8, ("2026-05", 1): 2},
            first_repeat_counts={("2026-05", 1): 2},
            latest_month_index=2026 * 12 + 6,
            max_offset=12,
        )
        cohort = result[0]
        assert cohort["per_period"][0] == 1.0
        assert cohort["per_period"][1] == 0.25
        assert cohort["per_period"][2] is None
        assert all(v is None for v in cohort["per_period"][2:])
        # cumulative censors from offset 2 onward too
        assert cohort["cumulative"][1] == 0.25
        assert all(v is None for v in cohort["cumulative"][2:])

    def test_cumulative_is_monotonic_non_decreasing(self):
        result = build_cohort_matrix(
            cohort_sizes={"2025-01": 100},
            per_period_counts={("2025-01", 0): 100},
            first_repeat_counts={
                ("2025-01", 1): 10,
                ("2025-01", 2): 5,
                ("2025-01", 4): 20,
            },
            latest_month_index=2026 * 12 + 6,
            max_offset=12,
        )
        cum = result[0]["cumulative"]
        observed = [v for v in cum if v is not None]
        assert observed == sorted(observed)
        assert cum[1] == 0.10
        assert cum[2] == 0.15   # +5
        assert cum[3] == 0.15   # no first-repeats at offset 3
        assert cum[4] == 0.35   # +20

    def test_same_month_only_repeat_keeps_cumulative_zero(self):
        # Customer repeats only within offset 0 → absent from first_repeat_counts.
        result = build_cohort_matrix(
            cohort_sizes={"2025-01": 5},
            per_period_counts={("2025-01", 0): 5},
            first_repeat_counts={},  # no later-month repeats at all
            latest_month_index=2026 * 12 + 6,
            max_offset=6,
        )
        cohort = result[0]
        assert cohort["per_period"][0] == 1.0
        assert all(v == 0.0 for v in cohort["cumulative"])

    def test_max_offset_controls_array_length(self):
        result = build_cohort_matrix(
            cohort_sizes={"2025-01": 10},
            per_period_counts={("2025-01", 0): 10},
            first_repeat_counts={},
            latest_month_index=2030 * 12 + 1,
            max_offset=6,
        )
        cohort = result[0]
        assert len(cohort["per_period"]) == 7   # offsets 0..6
        assert len(cohort["cumulative"]) == 7

    def test_cohorts_sorted_ascending(self):
        result = build_cohort_matrix(
            cohort_sizes={"2025-03": 1, "2025-01": 1, "2025-02": 1},
            per_period_counts={("2025-01", 0): 1, ("2025-02", 0): 1, ("2025-03", 0): 1},
            first_repeat_counts={},
            latest_month_index=2026 * 12 + 1,
            max_offset=3,
        )
        assert [c["cohort_month"] for c in result] == ["2025-01", "2025-02", "2025-03"]

    def test_observable_offset_with_no_activity_is_zero_not_null(self):
        # Offset 2 is observable (within horizon) but nobody active → 0.0, not None.
        result = build_cohort_matrix(
            cohort_sizes={"2025-01": 4},
            per_period_counts={("2025-01", 0): 4, ("2025-01", 1): 1},
            first_repeat_counts={("2025-01", 1): 1},
            latest_month_index=2025 * 12 + 6,
            max_offset=5,
        )
        cohort = result[0]
        assert cohort["per_period"][2] == 0.0
        assert cohort["per_period"][2] is not None


# ─────────────────────────────────────────────────────────────────────────────
# API tests — GET /analysis/cohort_retention
# ─────────────────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _clear_cache() -> None:
    import asyncio
    asyncio.run(analysis_cache.invalidate())


@pytest.fixture(autouse=True)
def _reset_cache():
    _clear_cache()
    yield
    _clear_cache()


_YOUZAN_HEADER = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,"
    "全部商品名称,商品种类数,订单实付金额"
)


def _row(order_id: int, date: str, phone: str, price: int = 10) -> str:
    return f"{order_id},{date},{phone},item,1,{price}"


def _upload(client, admin_token: str, rows: list[str], filename: str = "t.csv") -> None:
    csv_text = "\n".join([_YOUZAN_HEADER] + rows)
    r = client.post("/upload/", files={"file": (filename, csv_text)}, headers=_auth(admin_token))
    assert r.status_code == 202, r.json()


# Scenario shared across tests:
#   Phone A (...001): 2025-01, 2025-02, 2025-04  → cohort 2025-01, offsets 0,1,3
#   Phone B (...002): 2025-01                    → cohort 2025-01, offset 0 only
#   Phone C (...003): 2025-02, 2025-03           → cohort 2025-02, offsets 0,1
# latest activity month = 2025-04.
_SCENARIO = [
    _row(1, "2025-01-10", "13800000001"),
    _row(2, "2025-02-10", "13800000001"),
    _row(3, "2025-04-10", "13800000001"),
    _row(4, "2025-01-20", "13800000002"),
    _row(5, "2025-02-05", "13800000003"),
    _row(6, "2025-03-05", "13800000003"),
]


def _get(client, token, **params):
    r = client.get("/analysis/cohort_retention", params=params, headers=_auth(token))
    return r


class TestCohortRetentionEndpoint:
    def test_known_scenario_exact_cells(self, client, tokens):
        _upload(client, tokens["admin"], _SCENARIO)
        r = _get(client, tokens["analyst"], max_offset=6)
        assert r.status_code == 200, r.json()
        data = r.json()

        assert data["latest_data_month"] == "2025-04"
        assert data["max_offset"] == 6
        by_month = {c["cohort_month"]: c for c in data["cohorts"]}

        jan = by_month["2025-01"]
        assert jan["cohort_size"] == 2
        # offsets 0..3 observable (latest 2025-04), 4..6 censored
        assert jan["per_period"] == [1.0, 0.5, 0.0, 0.5, None, None, None]
        assert jan["cumulative"] == [0.0, 0.5, 0.5, 0.5, None, None, None]

        feb = by_month["2025-02"]
        assert feb["cohort_size"] == 1
        # offsets 0..2 observable (cohort 2025-02 + 2 = 2025-04), 3..6 censored
        assert feb["per_period"] == [1.0, 1.0, 0.0, None, None, None, None]
        assert feb["cumulative"] == [0.0, 1.0, 1.0, None, None, None, None]

    def test_cohorts_sorted_and_shape(self, client, tokens):
        _upload(client, tokens["admin"], _SCENARIO)
        data = _get(client, tokens["analyst"], max_offset=6).json()
        assert [c["cohort_month"] for c in data["cohorts"]] == ["2025-01", "2025-02"]
        for c in data["cohorts"]:
            assert len(c["per_period"]) == 7
            assert len(c["cumulative"]) == 7

    def test_acquisition_window_filters_cohorts(self, client, tokens):
        _upload(client, tokens["admin"], _SCENARIO)
        # Only cohorts whose first-order month is 2025-02 or later.
        data = _get(
            client, tokens["analyst"],
            start_date="2025-02-01", end_date="2025-12-31", max_offset=6,
        ).json()
        months = {c["cohort_month"] for c in data["cohorts"]}
        assert months == {"2025-02"}

    def test_platform_filter(self, client, tokens):
        _upload(client, tokens["admin"], _SCENARIO)
        # All scenario data is youzan; jd should yield no cohorts.
        data = _get(client, tokens["analyst"], platform="jd", max_offset=6).json()
        assert data["cohorts"] == []
        assert data["latest_data_month"] is None

    def test_max_offset_is_part_of_cache_key(self, client, tokens):
        _upload(client, tokens["admin"], _SCENARIO)
        d3 = _get(client, tokens["analyst"], max_offset=3).json()
        d6 = _get(client, tokens["analyst"], max_offset=6).json()
        assert len(d3["cohorts"][0]["per_period"]) == 4
        assert len(d6["cohorts"][0]["per_period"]) == 7

    def test_requires_analyst_role(self, client, tokens):
        r = _get(client, tokens["viewer"], max_offset=6)
        assert r.status_code == 403

    def test_requires_auth(self, client):
        r = client.get("/analysis/cohort_retention")
        assert r.status_code == 401

    def test_invalid_max_offset_rejected(self, client, tokens):
        r = _get(client, tokens["analyst"], max_offset=0)
        assert r.status_code == 422

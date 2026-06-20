"""Tests for the enhanced /analysis/repurchase_rate endpoint.

Covers:
1. Response shape — all new fields present, window_days echoed
2. All-time repurchase (no window_days)
3. Window-constrained repurchase (60 / 89 / 90 / 180 / 365 days)
4. avg_days_to_repurchase — correct average, window-aware, None when no repurchasers
5. frequency_distribution — correct 1/2/3/4+ bucketing, scoped to acquisition window
6. window_days is part of the cache key
7. Empty dataset / no new customers in window
8. Permission: viewer → 403, analyst/admin → 200
9. Validation: window_days < 1 → 422
"""

import asyncio
import pytest

from test_api_endpoints import client, tokens  # noqa: F401

from app.utils.cache import analysis_cache


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _clear_cache() -> None:
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
    r = client.post(
        "/upload/",
        files={"file": (filename, csv_text)},
        headers=_auth(admin_token),
    )
    assert r.status_code == 202, r.json()


def _get_rate(client, analyst_token: str, start: str, end: str, **extra) -> dict:
    params = {"start_date": start, "end_date": end, **extra}
    r = client.get(
        "/analysis/repurchase_rate",
        params=params,
        headers=_auth(analyst_token),
    )
    assert r.status_code == 200, r.json()
    return r.json()


# ── 1. Response shape ─────────────────────────────────────────────────────────

class TestRepurchaseRateShape:
    def test_all_new_fields_present(self, client, tokens):
        _upload(client, tokens["admin"], [_row(1, "2025-07-01", "13800000001")])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        for field in [
            "new_customers",
            "repurchasing_customers",
            "repurchase_rate",
            "avg_days_to_repurchase",
            "frequency_distribution",
            "window_days",
        ]:
            assert field in p, f"Missing field: {field}"

    def test_window_days_is_none_when_not_supplied(self, client, tokens):
        _upload(client, tokens["admin"], [_row(1, "2025-07-01", "13800000001")])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["window_days"] is None

    def test_window_days_echoed_when_supplied(self, client, tokens):
        _upload(client, tokens["admin"], [_row(1, "2025-07-01", "13800000001")])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=90)
        assert p["window_days"] == 90


# ── 2. All-time repurchase ────────────────────────────────────────────────────

class TestRepurchaseRateAllTime:
    def test_one_repurchaser_out_of_three(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-05", "13800000001"),  # A repurchases → in
            _row(3, "2025-07-02", "13800000002"),  # B: 1 order only
            _row(4, "2025-07-03", "13800000003"),  # C: 1 order only
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["new_customers"] == 3
        assert p["repurchasing_customers"] == 1
        assert pytest.approx(p["repurchase_rate"], rel=1e-6) == 1 / 3

    def test_no_repurchasers_gives_zero_rate(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-02", "13800000002"),
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["repurchasing_customers"] == 0
        assert p["repurchase_rate"] == 0.0
        assert p["avg_days_to_repurchase"] is None

    def test_all_repurchasers_gives_full_rate(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-10", "13800000001"),
            _row(3, "2025-07-01", "13800000002"),
            _row(4, "2025-07-15", "13800000002"),
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["new_customers"] == 2
        assert p["repurchasing_customers"] == 2
        assert pytest.approx(p["repurchase_rate"]) == 1.0

    def test_repurchase_outside_query_window_still_counts_all_time(self, client, tokens):
        """A second order that falls after end_date must count in all-time mode."""
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),   # first order — in acquisition window
            _row(2, "2025-12-15", "13800000001"),   # second order — far outside window
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["new_customers"] == 1
        assert p["repurchasing_customers"] == 1
        assert p["repurchase_rate"] == 1.0

    def test_customer_acquired_outside_window_not_counted(self, client, tokens):
        """Customer whose first order is before start_date must not appear as 'new'."""
        _upload(client, tokens["admin"], [
            _row(1, "2025-06-01", "13800000001"),  # first order before window
            _row(2, "2025-07-10", "13800000001"),  # repeat order inside window
            _row(3, "2025-07-01", "13800000002"),  # this customer IS new
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["new_customers"] == 1
        assert p["repurchasing_customers"] == 0


# ── 3. Window-constrained repurchase ─────────────────────────────────────────
#
# Test data layout (acquisition window: 2025-07-01 to 2025-07-31):
#
#   Customer 1 (13800000001): first = Jul 1, second = Aug 29  → 59 days
#   Customer 2 (13800000002): first = Jul 1, second = Sep 29  → 90 days
#   Customer 3 (13800000003): first = Jul 1, never repurchases

class TestRepurchaseRateWindowed:
    @pytest.fixture
    def scenario(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-08-29", "13800000001"),  # 59 days after first
            _row(3, "2025-07-01", "13800000002"),
            _row(4, "2025-09-29", "13800000002"),  # 90 days after first
            _row(5, "2025-07-01", "13800000003"),  # never repurchases
        ])

    def test_no_window_counts_both_repurchasers(self, client, tokens, scenario):
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["new_customers"] == 3
        assert p["repurchasing_customers"] == 2
        assert pytest.approx(p["repurchase_rate"], rel=1e-6) == 2 / 3

    def test_60d_window_includes_59d_repurchase(self, client, tokens, scenario):
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=60)
        assert p["new_customers"] == 3
        assert p["repurchasing_customers"] == 1  # only customer 1 at 59 days
        assert pytest.approx(p["repurchase_rate"], rel=1e-6) == 1 / 3

    def test_90d_window_includes_both_repurchasers(self, client, tokens, scenario):
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=90)
        assert p["new_customers"] == 3
        assert p["repurchasing_customers"] == 2  # 59 days and 90 days both qualify
        assert pytest.approx(p["repurchase_rate"], rel=1e-6) == 2 / 3

    def test_89d_window_excludes_90d_repurchaser(self, client, tokens, scenario):
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=89)
        assert p["repurchasing_customers"] == 1  # only customer 1 at 59 days

    def test_180d_window_includes_both_repurchasers(self, client, tokens, scenario):
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=180)
        assert p["repurchasing_customers"] == 2

    def test_365d_window_includes_both_repurchasers(self, client, tokens, scenario):
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=365)
        assert p["repurchasing_customers"] == 2

    def test_very_short_window_excludes_all(self, client, tokens, scenario):
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=10)
        assert p["repurchasing_customers"] == 0
        assert p["repurchase_rate"] == 0.0
        assert p["avg_days_to_repurchase"] is None


# ── 4. avg_days_to_repurchase ─────────────────────────────────────────────────

class TestAvgDaysToRepurchase:
    def test_simple_average_all_time(self, client, tokens):
        # Customer A: returns after 4 days
        # Customer B: returns after 10 days
        # Expected average: (4 + 10) / 2 = 7.0
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-05", "13800000001"),   # 4 days
            _row(3, "2025-07-01", "13800000002"),
            _row(4, "2025-07-11", "13800000002"),   # 10 days
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["avg_days_to_repurchase"] is not None
        assert pytest.approx(p["avg_days_to_repurchase"], abs=0.1) == 7.0

    def test_window_excludes_slow_repurchaser_from_average(self, client, tokens):
        # Customer 1: 59 days — qualifies under window_days=60
        # Customer 2: 90 days — does NOT qualify, must not affect the average
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-08-29", "13800000001"),   # 59 days
            _row(3, "2025-07-01", "13800000002"),
            _row(4, "2025-09-29", "13800000002"),   # 90 days
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=60)
        assert p["repurchasing_customers"] == 1
        assert pytest.approx(p["avg_days_to_repurchase"], abs=0.1) == 59.0

    def test_avg_days_is_none_when_no_repurchasers(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-02", "13800000002"),
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["avg_days_to_repurchase"] is None

    def test_avg_days_only_counts_per_customer_not_per_order(self, client, tokens):
        """A customer with 3 orders contributes only their first→second interval."""
        # Customer A: Jul 1 → Jul 5 → Jul 20
        # First-to-second gap: 4 days (not 19 days for first-to-third)
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-05", "13800000001"),
            _row(3, "2025-07-20", "13800000001"),
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["repurchasing_customers"] == 1
        assert pytest.approx(p["avg_days_to_repurchase"], abs=0.1) == 4.0


# ── 5. frequency_distribution ─────────────────────────────────────────────────

class TestFrequencyDistribution:
    def test_all_four_buckets_populated(self, client, tokens):
        _upload(client, tokens["admin"], [
            # 1 order → bucket "1"
            _row(1,  "2025-07-01", "13800000001"),
            # 2 orders → bucket "2"
            _row(2,  "2025-07-01", "13800000002"),
            _row(3,  "2025-07-10", "13800000002"),
            # 3 orders → bucket "3"
            _row(4,  "2025-07-01", "13800000003"),
            _row(5,  "2025-07-05", "13800000003"),
            _row(6,  "2025-07-20", "13800000003"),
            # 4 orders → bucket "4+"
            _row(7,  "2025-07-01", "13800000004"),
            _row(8,  "2025-07-05", "13800000004"),
            _row(9,  "2025-07-10", "13800000004"),
            _row(10, "2025-07-15", "13800000004"),
        ])
        dist = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")["frequency_distribution"]
        assert dist.get("1")  == 1
        assert dist.get("2")  == 1
        assert dist.get("3")  == 1
        assert dist.get("4+") == 1

    def test_five_orders_lands_in_4plus_bucket(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-02", "13800000001"),
            _row(3, "2025-07-03", "13800000001"),
            _row(4, "2025-07-04", "13800000001"),
            _row(5, "2025-07-05", "13800000001"),
        ])
        dist = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")["frequency_distribution"]
        assert dist.get("4+") == 1
        assert dist.get("1", 0) == 0

    def test_all_one_order_customers(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-02", "13800000002"),
            _row(3, "2025-07-03", "13800000003"),
        ])
        dist = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")["frequency_distribution"]
        assert dist.get("1") == 3
        assert dist.get("2", 0) == 0
        assert dist.get("3", 0) == 0
        assert dist.get("4+", 0) == 0

    def test_distribution_scoped_to_acquisition_window(self, client, tokens):
        """Customers first-acquired outside the query window must not appear in freq_dist."""
        _upload(client, tokens["admin"], [
            # this customer's first order is in August → outside July window
            _row(1, "2025-08-01", "13800000001"),
            _row(2, "2025-08-10", "13800000001"),
            # this customer IS in the July window
            _row(3, "2025-07-15", "13800000002"),
        ])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["new_customers"] == 1
        dist = p["frequency_distribution"]
        assert dist.get("1") == 1
        assert sum(dist.values()) == 1  # only the July customer counted

    def test_frequency_distribution_reflects_lifetime_orders(self, client, tokens):
        """Order count per customer uses lifetime history, not just the query window."""
        # Customer A first buys in July (in window), then again in November (outside)
        # Their lifetime order count is 2, so they land in bucket "2"
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-11-01", "13800000001"),  # outside query window but counts for lifetime
        ])
        dist = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")["frequency_distribution"]
        assert dist.get("2") == 1
        assert dist.get("1", 0) == 0


# ── 6. Cache key includes window_days ────────────────────────────────────────

class TestRepurchaseRateCacheKey:
    def test_different_windows_cached_separately(self, client, tokens):
        _upload(client, tokens["admin"], [
            _row(1, "2025-07-01", "13800000001"),
            _row(2, "2025-07-10", "13800000001"),
        ])
        h = _auth(tokens["analyst"])
        base = {"start_date": "2025-07-01", "end_date": "2025-07-31"}

        assert len(analysis_cache._cache) == 0
        client.get("/analysis/repurchase_rate", params=base, headers=h)
        assert len(analysis_cache._cache) == 1

        client.get("/analysis/repurchase_rate", params={**base, "window_days": 60}, headers=h)
        assert len(analysis_cache._cache) == 2

        client.get("/analysis/repurchase_rate", params={**base, "window_days": 90}, headers=h)
        assert len(analysis_cache._cache) == 3

    def test_same_window_reuses_cache_entry(self, client, tokens):
        _upload(client, tokens["admin"], [_row(1, "2025-07-01", "13800000001")])
        h = _auth(tokens["analyst"])
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31", "window_days": 90}

        client.get("/analysis/repurchase_rate", params=params, headers=h)
        assert len(analysis_cache._cache) == 1
        client.get("/analysis/repurchase_rate", params=params, headers=h)
        assert len(analysis_cache._cache) == 1  # cache hit, no new entry


# ── 7. Empty dataset ──────────────────────────────────────────────────────────

class TestRepurchaseRateEmpty:
    def test_no_new_customers_in_window(self, client, tokens):
        # Only order is before the query window
        _upload(client, tokens["admin"], [_row(1, "2025-06-01", "13800000001")])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31")
        assert p["new_customers"] == 0
        assert p["repurchasing_customers"] == 0
        assert p["repurchase_rate"] == 0.0
        assert p["avg_days_to_repurchase"] is None
        assert p["frequency_distribution"] == {}

    def test_windowed_with_no_customers(self, client, tokens):
        _upload(client, tokens["admin"], [_row(1, "2025-06-01", "13800000001")])
        p = _get_rate(client, tokens["analyst"], "2025-07-01", "2025-07-31", window_days=90)
        assert p["new_customers"] == 0
        assert p["repurchase_rate"] == 0.0
        assert p["frequency_distribution"] == {}


# ── 8. Permissions ────────────────────────────────────────────────────────────

class TestRepurchaseRatePermissions:
    def test_viewer_is_rejected(self, client, tokens):
        r = client.get(
            "/analysis/repurchase_rate",
            params={"start_date": "2025-07-01", "end_date": "2025-07-31"},
            headers=_auth(tokens["viewer"]),
        )
        assert r.status_code == 403

    def test_analyst_is_allowed(self, client, tokens):
        _upload(client, tokens["admin"], [_row(1, "2025-07-01", "13800000001")])
        r = client.get(
            "/analysis/repurchase_rate",
            params={"start_date": "2025-07-01", "end_date": "2025-07-31"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200

    def test_admin_is_allowed(self, client, tokens):
        _upload(client, tokens["admin"], [_row(1, "2025-07-01", "13800000001")])
        r = client.get(
            "/analysis/repurchase_rate",
            params={"start_date": "2025-07-01", "end_date": "2025-07-31"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 200


# ── 9. Input validation ───────────────────────────────────────────────────────

class TestRepurchaseRateValidation:
    def test_window_days_zero_is_rejected(self, client, tokens):
        r = client.get(
            "/analysis/repurchase_rate",
            params={"start_date": "2025-07-01", "end_date": "2025-07-31", "window_days": 0},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 422

    def test_window_days_negative_is_rejected(self, client, tokens):
        r = client.get(
            "/analysis/repurchase_rate",
            params={"start_date": "2025-07-01", "end_date": "2025-07-31", "window_days": -1},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 422

    def test_missing_start_date_is_rejected(self, client, tokens):
        r = client.get(
            "/analysis/repurchase_rate",
            params={"end_date": "2025-07-31"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 422

    def test_missing_end_date_is_rejected(self, client, tokens):
        r = client.get(
            "/analysis/repurchase_rate",
            params={"start_date": "2025-07-01"},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 422

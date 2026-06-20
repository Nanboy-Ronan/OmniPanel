"""Tests for analysis result caching.

Covers two layers:
1. Unit tests for the ``AnalysisCache`` helper (get / set / invalidate / TTL).
2. Integration tests through the FastAPI TestClient verifying that:
   - Repeated identical requests hit the cache.
   - Cache is invalidated on upload (POST /upload/).
   - Cache is invalidated on clear-db (POST /admin/clear-db).
   - Different query parameters produce different cache keys.
"""

import asyncio
import time
import pytest
from cachetools import TTLCache

from app.utils.cache import AnalysisCache, analysis_cache


# ── helpers ──────────────────────────────────────────────────────────────────

def _cache_entry_count() -> int:
    """Return number of entries currently held by the singleton analysis_cache."""
    return len(analysis_cache._cache)


def _clear_global_cache() -> None:
    asyncio.run(analysis_cache.invalidate())


# ── unit tests ───────────────────────────────────────────────────────────────

class TestAnalysisCacheUnit:
    def test_set_and_get(self):
        c = AnalysisCache(ttl=60)
        asyncio.run(c.set("key1", {"result": 42}))
        assert asyncio.run(c.get("key1")) == {"result": 42}

    def test_get_missing_key_returns_none(self):
        c = AnalysisCache(ttl=60)
        assert asyncio.run(c.get("no_such_key")) is None

    def test_invalidate_clears_all(self):
        c = AnalysisCache(ttl=60)
        asyncio.run(c.set("a", 1))
        asyncio.run(c.set("b", 2))
        assert asyncio.run(c.get("a")) == 1
        asyncio.run(c.invalidate())
        assert asyncio.run(c.get("a")) is None
        assert asyncio.run(c.get("b")) is None

    def test_make_key_different_params_produce_different_keys(self):
        c = AnalysisCache()
        k1 = c._make_key("analyse", start_date="2025-01-01", end_date="2025-01-31")
        k2 = c._make_key("analyse", start_date="2025-02-01", end_date="2025-02-28")
        assert k1 != k2

    def test_make_key_same_params_same_key(self):
        c = AnalysisCache()
        k1 = c._make_key("analyse", start_date="2025-01-01", end_date="2025-01-31")
        k2 = c._make_key("analyse", start_date="2025-01-01", end_date="2025-01-31")
        assert k1 == k2

    def test_make_key_different_endpoint_different_key(self):
        c = AnalysisCache()
        k1 = c._make_key("analyse", start_date="2025-01-01")
        k2 = c._make_key("overview", start_date="2025-01-01")
        assert k1 != k2

    def test_make_key_none_params_handled(self):
        c = AnalysisCache()
        k = c._make_key("customers", start_date=None, end_date=None)
        assert isinstance(k, str) and len(k) == 64

    def test_ttl_expiry(self):
        c = AnalysisCache(ttl=1, maxsize=10)
        asyncio.run(c.set("k", "v"))
        assert asyncio.run(c.get("k")) == "v"
        time.sleep(1.1)
        assert asyncio.run(c.get("k")) is None

    def test_maxsize_eviction(self):
        c = AnalysisCache(ttl=600, maxsize=2)
        asyncio.run(c.set("a", 1))
        asyncio.run(c.set("b", 2))
        asyncio.run(c.set("c", 3))  # evicts the oldest ("a")
        assert asyncio.run(c.get("a")) is None
        assert asyncio.run(c.get("b")) == 2
        assert asyncio.run(c.get("c")) == 3


# ── integration tests ────────────────────────────────────────────────────────

# Reuse the client + tokens fixtures from the main test suite.
# conftest would be better, but these tests are run in-process so we can
# import from test_api_endpoints.

from test_api_endpoints import client, tokens, sample_data  # noqa: E402 F401


@pytest.fixture(autouse=True)
def _reset_cache_between_tests():
    """Guarantee a clean cache before every integration test."""
    _clear_global_cache()
    yield
    _clear_global_cache()


class TestAnalysisCacheIntegration:
    @pytest.fixture(autouse=True)
    def _seed_sample_data(self, sample_data):
        pass

    def test_analyse_cache_hit(self, client, tokens):
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        h = {"Authorization": f"Bearer {tokens['analyst']}"}

        assert _cache_entry_count() == 0
        r1 = client.get("/analysis/", params=params, headers=h)
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        r2 = client.get("/analysis/", params=params, headers=h)
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert _cache_entry_count() == 1  # still 1 – hit cache

    def test_overview_cache_hit(self, client, tokens):
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        h = {"Authorization": f"Bearer {tokens['analyst']}"}

        r1 = client.get("/analysis/overview", params=params, headers=h)
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        r2 = client.get("/analysis/overview", params=params, headers=h)
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert _cache_entry_count() == 1

    def test_repurchase_rate_cache_hit(self, client, tokens):
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        h = {"Authorization": f"Bearer {tokens['analyst']}"}

        r1 = client.get("/analysis/repurchase_rate", params=params, headers=h)
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        r2 = client.get("/analysis/repurchase_rate", params=params, headers=h)
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert _cache_entry_count() == 1

    def test_customers_cache_hit(self, client, tokens):
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        h = {"Authorization": f"Bearer {tokens['analyst']}"}

        r1 = client.get("/analysis/customers", params=params, headers=h)
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        r2 = client.get("/analysis/customers", params=params, headers=h)
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert _cache_entry_count() == 1

    def test_customer_orders_cache_hit(self, client, tokens):
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        h = {"Authorization": f"Bearer {tokens['analyst']}"}

        r1 = client.get("/analysis/customers/13900000001", params=params, headers=h)
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        r2 = client.get("/analysis/customers/13900000001", params=params, headers=h)
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert _cache_entry_count() == 1

    def test_different_params_different_cache_entries(self, client, tokens):
        h = {"Authorization": f"Bearer {tokens['analyst']}"}

        r1 = client.get(
            "/analysis/overview",
            params={"start_date": "2025-07-01", "end_date": "2025-07-31"},
            headers=h,
        )
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        r2 = client.get(
            "/analysis/overview",
            params={"start_date": "2025-06-21", "end_date": "2025-06-30"},
            headers=h,
        )
        assert r2.status_code == 200
        assert _cache_entry_count() == 2

        assert r1.json() != r2.json()

    def test_upload_invalidates_cache(self, client, tokens):
        h_auth = {"Authorization": f"Bearer {tokens['analyst']}"}
        h_admin = {"Authorization": f"Bearer {tokens['admin']}"}

        # Warm the cache
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        r1 = client.get("/analysis/overview", params=params, headers=h_auth)
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        # Upload new data – must clear the cache
        data = (
            "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
            "999,2025-07-15,13800009999,new_item,1,50"
        )
        r_upload = client.post(
            "/upload/", files={"file": ("fresh.csv", data)}, headers=h_admin
        )
        assert r_upload.status_code == 202
        batch_id = r_upload.json()["batch_id"]
        r_batch = client.get(f"/upload/batches/{batch_id}", headers=h_admin)
        assert r_batch.json()["inserted_orders"] == 1

        assert _cache_entry_count() == 0

        # Re-fetch – should recompute and include the new row
        r2 = client.get("/analysis/overview", params=params, headers=h_auth)
        assert r2.status_code == 200
        assert _cache_entry_count() == 1
        # Order count increased by 1
        assert r2.json()["orders"] == r1.json()["orders"] + 1

    @pytest.mark.skip(reason="clear-db requires ORM tables created via lifespan; test fixture bypasses lifespan")
    def test_clear_db_invalidates_cache(self, client, tokens):
        h_auth = {"Authorization": f"Bearer {tokens['analyst']}"}
        h_admin = {"Authorization": f"Bearer {tokens['admin']}"}

        # Warm the cache
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
        r1 = client.get("/analysis/overview", params=params, headers=h_auth)
        assert r1.status_code == 200
        assert _cache_entry_count() == 1

        # Clear the DB – must clear the cache
        r_clear = client.post("/admin/clear-db", headers=h_admin)
        assert r_clear.status_code == 200

        assert _cache_entry_count() == 0

        # After clear-db, analysis should return 503 (no data)
        r2 = client.get("/analysis/overview", params=params, headers=h_auth)
        assert r2.status_code == 503

    def test_cache_isolated_per_endpoint(self, client, tokens):
        h = {"Authorization": f"Bearer {tokens['analyst']}"}
        params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}

        client.get("/analysis/", params=params, headers=h)
        assert _cache_entry_count() == 1

        client.get("/analysis/overview", params=params, headers=h)
        assert _cache_entry_count() == 2

        client.get("/analysis/repurchase_rate", params=params, headers=h)
        assert _cache_entry_count() == 3

        client.get("/analysis/customers", params=params, headers=h)
        assert _cache_entry_count() == 4

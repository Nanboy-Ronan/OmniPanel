"""Tests for cross-platform real-customer identity clustering (跨平台真实客户身份统一).

Two layers:
  1. Pure-function tests for `build_phone_clusters()` — no DB required.
  2. API-level tests against `GET /analysis/identity/clusters`, using
     deliberately overlapping phone numbers across platforms (the shared
     fixtures in tests/test_multiplatform.py don't overlap by design, so the
     matching path needs its own CSVs).
"""
from __future__ import annotations

import pytest

from app.views.ecommerce.identity import build_phone_clusters

from tests.test_multiplatform import api_client, api_tokens, _auth  # noqa: F401


def _row(customer_key, platform, phone, orders=1, revenue=100.0, first="2025-01-01", last="2025-01-01"):
    return {
        "customer_key": customer_key,
        "platform": platform,
        "phone": phone,
        "orders": orders,
        "revenue": revenue,
        "first_date": first,
        "last_date": last,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pure-function tests — build_phone_clusters()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPhoneClustersExactTier:
    def test_youzan_and_tmall_same_full_phone_join_as_exact(self):
        rows = [
            _row("13800006198", "youzan", "13800006198", orders=2, revenue=200.0),
            _row("江苏省扬州市广陵区淮海路16号", "tmall", "13800006198", orders=1, revenue=50.0),
        ]
        result = build_phone_clusters(rows)
        assert len(result["exact"]) == 1
        cluster = result["exact"][0]
        assert cluster["confidence"] == "exact"
        assert cluster["platforms"] == ["tmall", "youzan"]
        assert cluster["customer_keys"]["youzan"] == ["13800006198"]
        assert cluster["customer_keys"]["tmall"] == ["江苏省扬州市广陵区淮海路16号"]
        assert cluster["order_count"] == 3
        assert cluster["revenue"] == 250.0
        assert result["fuzzy"] == []

    def test_single_platform_customer_with_full_phone_is_its_own_exact_cluster(self):
        rows = [_row("13800001111", "youzan", "13800001111")]
        result = build_phone_clusters(rows)
        assert len(result["exact"]) == 1
        assert result["exact"][0]["platforms"] == ["youzan"]

    def test_missing_or_unusable_phone_excluded_entirely(self):
        rows = [_row("某地址", "tmall", None), _row("某地址2", "tmall", "")]
        result = build_phone_clusters(rows)
        assert result["exact"] == []
        assert result["fuzzy"] == []


class TestBuildPhoneClustersFuzzyTier:
    def test_jd_masked_phone_attaches_to_matching_exact_cluster(self):
        rows = [
            _row("13800006198", "youzan", "13800006198", orders=2, revenue=200.0),
            _row("广东深圳市福田区农园路66号", "jd", "1******6198", orders=1, revenue=80.0),
        ]
        result = build_phone_clusters(rows)
        assert len(result["exact"]) == 1
        assert len(result["fuzzy"]) == 1

        exact_cluster = result["exact"][0]
        # The exact cluster's own numbers must be unaffected by the JD attachment.
        assert exact_cluster["order_count"] == 2
        assert exact_cluster["revenue"] == 200.0

        fuzzy_cluster = result["fuzzy"][0]
        assert fuzzy_cluster["confidence"] == "fuzzy"
        assert fuzzy_cluster["attached_to"] == "13800006198"
        assert fuzzy_cluster["platforms"] == ["jd"]
        assert fuzzy_cluster["order_count"] == 1
        assert fuzzy_cluster["revenue"] == 80.0

    def test_jd_masked_phone_with_no_match_becomes_standalone_fuzzy_cluster(self):
        rows = [_row("某地址", "jd", "1******9993", orders=1, revenue=300.0)]
        result = build_phone_clusters(rows)
        assert result["exact"] == []
        assert len(result["fuzzy"]) == 1
        cluster = result["fuzzy"][0]
        assert cluster["attached_to"] is None
        assert cluster["platforms"] == ["jd"]

    def test_two_different_jd_customers_with_colliding_fingerprint_are_grouped(self):
        """Fingerprint collisions between distinct real people are expected
        fuzziness, not a bug — assert it happens rather than that it doesn't."""
        rows = [
            _row("地址甲", "jd", "1******9993", orders=1, revenue=100.0),
            _row("地址乙", "jd", "1******9993", orders=1, revenue=150.0),
        ]
        result = build_phone_clusters(rows)
        assert len(result["fuzzy"]) == 1
        cluster = result["fuzzy"][0]
        assert sorted(cluster["customer_keys"]["jd"]) == ["地址乙", "地址甲"]
        assert cluster["order_count"] == 2
        assert cluster["revenue"] == 250.0

    def test_jd_fingerprint_matching_multiple_exact_clusters_is_ambiguous_and_not_attached(self):
        """If two distinct full phones share the same last-4-digit fingerprint,
        a JD row matching that fingerprint can't be disambiguated — it must NOT
        be attached to either exact cluster (that would double-count it)."""
        rows = [
            _row("13800006198", "youzan", "13800006198"),
            _row("地址A", "tmall", "19900006198"),
            _row("地址B", "jd", "1******6198", orders=1, revenue=999.0),
        ]
        result = build_phone_clusters(rows)
        assert len(result["exact"]) == 2
        for cluster in result["exact"]:
            assert cluster["order_count"] == 1  # neither absorbed the JD row

        assert len(result["fuzzy"]) == 1
        assert result["fuzzy"][0]["attached_to"] is None
        assert result["fuzzy"][0]["revenue"] == 999.0


# ─────────────────────────────────────────────────────────────────────────────
# API-level tests — GET /analysis/identity/clusters
# ─────────────────────────────────────────────────────────────────────────────

def _csv_youzan_with_phone(phone: str, name: str = "张三") -> str:
    return (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额,"
        "收货人/提货人,收货人省份,收货人地区,详细收货地址/提货地址,买家昵称,优惠券码名称,分销员\n"
        f"YZ-ID-001,2025-08-01 10:00:00,{phone},山参精华,1,200.00,"
        f"{name},广东省,深圳市,广东省深圳市福田区某路1号,buyer1,,导购A\n"
    )


def _csv_tmall_with_phone(phone: str, name: str = "李四") -> str:
    return (
        "订单编号,总金额,收货地址,订单创建时间,商品标题\n"
        f"TM-ID-001,150,{name}，86-{phone}，江苏省 苏州市 姑苏区 某路2号,2025-08-05 09:00:00,山参精华片\n"
    )


def _csv_jd_with_masked_phone(masked_phone: str, address: str, name: str = "王五") -> str:
    return (
        "订单号,商品名称,订购数量,下单时间,订单金额,客户姓名,客户地址,联系电话,京东价\n"
        f"JD-ID-001,山参精华片,1,2025-08-10 11:00:00,90.00,{name},{address},{masked_phone},90.00\n"
    )


class TestIdentityClustersEndpoint:
    def test_exact_match_across_youzan_and_tmall(self, api_client, api_tokens):
        shared_phone = "13800006198"
        api_client.post(
            "/upload/",
            files={"file": ("yz.csv", _csv_youzan_with_phone(shared_phone))},
            headers=_auth(api_tokens["admin"]),
        )
        api_client.post(
            "/upload/",
            files={"file": ("tm.csv", _csv_tmall_with_phone(shared_phone))},
            headers=_auth(api_tokens["admin"]),
        )

        r = api_client.get(
            "/analysis/identity/clusters",
            params={"start_date": "2025-01-01", "end_date": "2027-01-01"},
            headers=_auth(api_tokens["analyst"]),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["exact"]["cluster_count"] >= 1
        matching = [
            c for c in data["exact"]["clusters"]
            if set(c["platforms"]) == {"youzan", "tmall"}
        ]
        assert len(matching) == 1
        assert matching[0]["order_count"] == 2

    def test_jd_fuzzy_match_kept_separate_from_exact(self, api_client, api_tokens):
        shared_phone = "13900006198"
        api_client.post(
            "/upload/",
            files={"file": ("yz2.csv", _csv_youzan_with_phone(shared_phone, name="赵六"))},
            headers=_auth(api_tokens["admin"]),
        )
        api_client.post(
            "/upload/",
            files={"file": ("jd2.csv", _csv_jd_with_masked_phone("1******6198", "广东深圳市南山区某路9号"))},
            headers=_auth(api_tokens["admin"]),
        )

        r = api_client.get(
            "/analysis/identity/clusters",
            params={"start_date": "2025-01-01", "end_date": "2027-01-01"},
            headers=_auth(api_tokens["analyst"]),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "caveat" in data["fuzzy"]

        exact_match = [c for c in data["exact"]["clusters"] if c["platforms"] == ["youzan"]]
        assert exact_match, "Youzan-only exact cluster should still exist"
        assert exact_match[0]["order_count"] == 1  # unaffected by the JD attachment

        fuzzy_match = [c for c in data["fuzzy"]["clusters"] if c["attached_to"] == shared_phone]
        assert len(fuzzy_match) == 1
        assert fuzzy_match[0]["order_count"] == 1

    def test_confidence_query_param_filters_response(self, api_client, api_tokens):
        api_client.post(
            "/upload/",
            files={"file": ("yz3.csv", _csv_youzan_with_phone("13700001234", name="孙七"))},
            headers=_auth(api_tokens["admin"]),
        )
        r = api_client.get(
            "/analysis/identity/clusters",
            params={"start_date": "2025-01-01", "end_date": "2027-01-01", "confidence": "exact"},
            headers=_auth(api_tokens["analyst"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert list(data.keys()) == ["exact"]

    def test_requires_analyst_role(self, api_client, api_tokens):
        api_client.post(
            "/admin/users",
            json={"email": "viewer@mptest.com", "password": "pw", "role": "viewer"},
            headers=_auth(api_tokens["admin"]),
        )
        r_login = api_client.post(
            "/auth/jwt/login",
            data={"username": "viewer@mptest.com", "password": "pw"},
        )
        viewer_token = r_login.json()["access_token"]

        r = api_client.get(
            "/analysis/identity/clusters",
            headers=_auth(viewer_token),
        )
        assert r.status_code == 403

    def test_requires_auth(self, api_client):
        r = api_client.get("/analysis/identity/clusters")
        assert r.status_code == 401


class TestIdentityClustersDoesNotAffectExistingCustomerEndpoint:
    """Regression: this new feature must not change the existing per-platform
    /analysis/customers behavior at all."""

    def test_customers_endpoint_unaffected_by_identity_feature(self, api_client, api_tokens):
        phone = "13600001111"
        api_client.post(
            "/upload/",
            files={"file": ("yz4.csv", _csv_youzan_with_phone(phone, name="周八"))},
            headers=_auth(api_tokens["admin"]),
        )
        api_client.post(
            "/upload/",
            files={"file": ("tm4.csv", _csv_tmall_with_phone(phone, name="吴九"))},
            headers=_auth(api_tokens["admin"]),
        )

        r = api_client.get(
            "/analysis/customers",
            params={"start_date": "2025-01-01", "end_date": "2027-01-01"},
            headers=_auth(api_tokens["analyst"]),
        )
        assert r.status_code == 200
        data = r.json()
        # Still two separate per-platform customers — identity clustering is
        # an additive, separate view, not a replacement.
        keys = {row["customer_key"] for row in data}
        assert phone in keys  # youzan's customer_key IS the phone
        assert any(k != phone for k in keys)  # tmall's customer_key is an address

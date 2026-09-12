"""Tests for POST /media/xhs/upload_overview.

Mirrors tests/test_xhs.py's TestXhsUploadEndpoint structure, but for the
JSON payload collect_xhs_overview() produces instead of an xlsx export.
"""
from __future__ import annotations

import json

from test_api_endpoints import client, tokens  # noqa: F401
from test_xhs import account, _auth  # noqa: F401


def _payload_bytes(daily=None, audience_source=None) -> bytes:
    return json.dumps({
        "daily": daily if daily is not None else [{"metric_date": "2026-09-01", "view_count": 10}],
        "audience_source": audience_source if audience_source is not None else [],
    }).encode("utf-8")


class TestUploadXhsOverviewEndpoint:
    def test_valid_payload_upserts_and_returns_counts(self, client, tokens, account):
        r = client.post(
            "/media/xhs/upload_overview",
            data={"account_id": account},
            files={"file": ("overview.json", _payload_bytes(), "application/json")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["daily_upserted"] == 1
        assert body["audience_source_upserted"] == 0

    def test_unknown_account_id_404s(self, client, tokens):
        r = client.post(
            "/media/xhs/upload_overview",
            data={"account_id": 999999},
            files={"file": ("overview.json", _payload_bytes(), "application/json")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 404

    def test_invalid_json_returns_400_not_500(self, client, tokens, account):
        r = client.post(
            "/media/xhs/upload_overview",
            data={"account_id": account},
            files={"file": ("overview.json", b"not json{{{", "application/json")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_empty_payload_returns_400(self, client, tokens, account):
        r = client.post(
            "/media/xhs/upload_overview",
            data={"account_id": account},
            files={"file": ("overview.json", _payload_bytes(daily=[], audience_source=[]), "application/json")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 400

    def test_reupload_same_date_overwrites_not_duplicates(self, client, tokens, account):
        client.post(
            "/media/xhs/upload_overview",
            data={"account_id": account},
            files={"file": ("overview.json",
                            _payload_bytes(daily=[{"metric_date": "2026-09-02", "view_count": 5}]),
                            "application/json")},
            headers=_auth(tokens["analyst"]),
        )
        r = client.post(
            "/media/xhs/upload_overview",
            data={"account_id": account},
            files={"file": ("overview.json",
                            _payload_bytes(daily=[{"metric_date": "2026-09-02", "view_count": 9}]),
                            "application/json")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200
        assert r.json()["daily_upserted"] == 1

    def test_audience_source_rows_upsert(self, client, tokens, account):
        r = client.post(
            "/media/xhs/upload_overview",
            data={"account_id": account},
            files={"file": ("overview.json",
                            _payload_bytes(daily=[], audience_source=[
                                {"window": "seven", "source_type": 1, "title": "首页推荐", "value_pct": 65.0},
                            ]),
                            "application/json")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["audience_source_upserted"] == 1

    def test_unauthenticated_request_401s(self, client, account):
        r = client.post(
            "/media/xhs/upload_overview",
            data={"account_id": account},
            files={"file": ("overview.json", _payload_bytes(), "application/json")},
        )
        assert r.status_code == 401

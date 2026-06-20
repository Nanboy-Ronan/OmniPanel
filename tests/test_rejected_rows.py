"""Tests for GET /upload/batches/{batch_id}/rejected.

TDD: these tests are written before the endpoint is implemented.

Covers:
1. Clean upload → rejected list is empty (count 0, rows []).
2. Upload with invalid rows → rejected list returns them with correct count.
3. Each rejected row has the expected fields: id, source_row_number, reason, raw_payload.
4. The reason string names the missing field(s).
5. batch_id in the response matches the requested batch.
6. 404 for an unknown batch_id.
7. Unauthenticated request → 401.
8. Viewer role can access (read-only endpoint, no privilege needed beyond login).
"""

import pytest

from test_api_endpoints import client, tokens  # noqa: F401


# ── helpers ───────────────────────────────────────────────────────────────────

_YZ_HEADER = (
    "订单号,买家付款时间,收货人手机号/提货人手机号,"
    "全部商品名称,商品种类数,订单实付金额"
)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload_and_get_batch_id(client, admin_token: str, csv_text: str) -> int:
    r = client.post(
        "/upload/",
        files={"file": ("orders.csv", csv_text)},
        headers=_auth(admin_token),
    )
    assert r.status_code == 202, r.text
    return r.json()["batch_id"]


# ── CSV fixtures ──────────────────────────────────────────────────────────────

def _clean_csv() -> str:
    """Three valid Youzan rows — no rejections expected."""
    return "\n".join([
        _YZ_HEADER,
        "1001,2025-07-01,13800001111,Item A,1,99.00",
        "1002,2025-07-02,13800002222,Item B,1,50.00",
        "1003,2025-07-03,13800003333,Item C,2,120.00",
    ])


def _mixed_csv() -> str:
    """One valid row + two invalid rows (blank date / blank phone)."""
    return "\n".join([
        _YZ_HEADER,
        "1001,2025-07-01,13800001111,Item A,1,99.00",   # valid
        "1002,,13800002222,Item B,1,50.00",             # invalid: blank order_date
        "1003,2025-07-03,,Item C,1,30.00",              # invalid: blank phone → no customer_key
    ])


# ── tests ─────────────────────────────────────────────────────────────────────

class TestRejectedRowsEmpty:
    def test_clean_upload_returns_empty_list(self, client, tokens):
        batch_id = _upload_and_get_batch_id(client, tokens["admin"], _clean_csv())
        r = client.get(
            f"/upload/batches/{batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["rows"] == []

    def test_response_contains_batch_id(self, client, tokens):
        batch_id = _upload_and_get_batch_id(client, tokens["admin"], _clean_csv())
        r = client.get(
            f"/upload/batches/{batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 200
        assert r.json()["batch_id"] == batch_id


class TestRejectedRowsContent:
    @pytest.fixture(autouse=True)
    def _upload(self, client, tokens):
        self.batch_id = _upload_and_get_batch_id(client, tokens["admin"], _mixed_csv())

    def test_count_matches_invalid_rows(self, client, tokens):
        r = client.get(
            f"/upload/batches/{self.batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert len(body["rows"]) == 2

    def test_each_row_has_required_fields(self, client, tokens):
        r = client.get(
            f"/upload/batches/{self.batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        for row in r.json()["rows"]:
            assert "id" in row
            assert "source_row_number" in row
            assert "reason" in row
            assert "raw_payload" in row

    def test_source_row_numbers_are_distinct(self, client, tokens):
        r = client.get(
            f"/upload/batches/{self.batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        nums = [row["source_row_number"] for row in r.json()["rows"]]
        assert len(set(nums)) == 2

    def test_reason_mentions_missing_field(self, client, tokens):
        r = client.get(
            f"/upload/batches/{self.batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        reasons = [row["reason"] for row in r.json()["rows"]]
        assert any("order_date" in reason or "customer_key" in reason for reason in reasons)

    def test_raw_payload_is_dict(self, client, tokens):
        r = client.get(
            f"/upload/batches/{self.batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        for row in r.json()["rows"]:
            assert isinstance(row["raw_payload"], dict)

    def test_batch_id_in_response_matches(self, client, tokens):
        r = client.get(
            f"/upload/batches/{self.batch_id}/rejected",
            headers=_auth(tokens["admin"]),
        )
        assert r.json()["batch_id"] == self.batch_id


class TestRejectedRowsErrors:
    def test_unknown_batch_returns_404(self, client, tokens):
        r = client.get(
            "/upload/batches/999999/rejected",
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 404

    def test_unauthenticated_returns_401(self, client, tokens):
        # Upload a batch first so there is a real batch_id to query
        batch_id = _upload_and_get_batch_id(client, tokens["admin"], _clean_csv())
        r = client.get(f"/upload/batches/{batch_id}/rejected")
        assert r.status_code == 401

    def test_analyst_can_access(self, client, tokens):
        batch_id = _upload_and_get_batch_id(client, tokens["admin"], _clean_csv())
        r = client.get(
            f"/upload/batches/{batch_id}/rejected",
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 200

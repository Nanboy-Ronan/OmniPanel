"""Tests for XhsAccount CRUD and account-scoped post isolation.

TDD spec — written before implementation.

Covered behaviours
──────────────────
TestXhsAccountCRUD
  1. POST /media/xhs/accounts  — admin creates an account, returns {id, name, is_active}
  2. GET  /media/xhs/accounts  — any authenticated user can list accounts
  3. DELETE /media/xhs/accounts/{id}  — admin can delete; cascades posts
  4. Non-admin cannot create or delete accounts (403)
  5. Unauthenticated requests → 401

TestXhsAccountIsolation
  6. Uploading to account A does not affect account B's post list
  7. (title, publish_date) dedup is per-account: same title on two accounts → two rows
  8. GET /media/xhs/posts?account_id=X returns only that account's posts
  9. Deleting an account cascades and removes its posts

TestXhsUploadRequiresAccount
  10. POST /media/xhs/upload without account_id → 422 (validation error)
  11. POST /media/xhs/upload with unknown account_id → 404
"""
from __future__ import annotations

import io
import pytest
import pandas as pd

from test_api_endpoints import client, tokens  # noqa: F401


# ── helpers ───────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_account(client, admin_token: str, name: str = "测试账号A") -> dict:
    r = client.post(
        "/media/xhs/accounts",
        json={"name": name},
        headers=_auth(admin_token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_xhs_xlsx_bytes(title: str = "测试笔记", date_str: str = "2026年04月01日00时00分00秒") -> bytes:
    headers = [
        "笔记标题", "首次发布时间", "体裁",
        "曝光", "观看量", "封面点击率",
        "点赞", "评论", "收藏", "涨粉", "分享", "人均观看时长", "弹幕",
    ]
    banner = {h: "最多导出排序后前1000条笔记" for h in headers}
    real_headers = {h: h for h in headers}
    row = {
        "笔记标题": title, "首次发布时间": date_str, "体裁": "图文",
        "曝光": "100", "观看量": "50", "封面点击率": "0.05",
        "点赞": "5", "评论": "1", "收藏": "2", "涨粉": "1", "分享": "1",
        "人均观看时长": "30.0", "弹幕": "0",
    }
    df = pd.DataFrame([banner, real_headers, row], columns=headers)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    return buf.getvalue()


def _upload(client, token: str, account_id: int, title: str = "测试笔记",
            date_str: str = "2026年04月01日00时00分00秒") -> dict:
    r = client.post(
        "/media/xhs/upload",
        data={"account_id": account_id},
        files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(title, date_str),
                        "application/octet-stream")},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── TestXhsAccountCRUD ────────────────────────────────────────────────────────

class TestXhsAccountCRUD:

    def test_admin_can_create_account(self, client, tokens):
        r = client.post(
            "/media/xhs/accounts",
            json={"name": "示例账号A"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 201

    def test_create_returns_id_name_is_active(self, client, tokens):
        r = client.post(
            "/media/xhs/accounts",
            json={"name": "示例账号B"},
            headers=_auth(tokens["admin"]),
        )
        body = r.json()
        assert "id" in body
        assert body["name"] == "示例账号B"
        assert body["is_active"] is True

    def test_list_accounts_authenticated(self, client, tokens):
        _create_account(client, tokens["admin"], "列表测试账号")
        r = client.get("/media/xhs/accounts", headers=_auth(tokens["analyst"]))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_contains_created_account(self, client, tokens):
        acc = _create_account(client, tokens["admin"], "可见账号")
        r = client.get("/media/xhs/accounts", headers=_auth(tokens["viewer"]))
        ids = [a["id"] for a in r.json()]
        assert acc["id"] in ids

    def test_admin_can_delete_account(self, client, tokens):
        acc = _create_account(client, tokens["admin"], "待删账号")
        r = client.delete(
            f"/media/xhs/accounts/{acc['id']}",
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 204

    def test_delete_removes_from_list(self, client, tokens):
        acc = _create_account(client, tokens["admin"], "删后消失账号")
        client.delete(f"/media/xhs/accounts/{acc['id']}", headers=_auth(tokens["admin"]))
        r = client.get("/media/xhs/accounts", headers=_auth(tokens["admin"]))
        ids = [a["id"] for a in r.json()]
        assert acc["id"] not in ids

    def test_non_admin_cannot_create(self, client, tokens):
        for role in ("analyst", "viewer"):
            r = client.post(
                "/media/xhs/accounts",
                json={"name": f"{role}账号"},
                headers=_auth(tokens[role]),
            )
            assert r.status_code == 403, f"{role} should get 403"

    def test_non_admin_cannot_delete(self, client, tokens):
        acc = _create_account(client, tokens["admin"], "禁止删除账号")
        for role in ("analyst", "viewer"):
            r = client.delete(
                f"/media/xhs/accounts/{acc['id']}",
                headers=_auth(tokens[role]),
            )
            assert r.status_code == 403, f"{role} should get 403"

    def test_unauthenticated_create_returns_401(self, client, tokens):
        r = client.post("/media/xhs/accounts", json={"name": "未登录"})
        assert r.status_code == 401

    def test_unauthenticated_list_returns_401(self, client, tokens):
        r = client.get("/media/xhs/accounts")
        assert r.status_code == 401

    def test_delete_nonexistent_returns_404(self, client, tokens):
        r = client.delete("/media/xhs/accounts/999999", headers=_auth(tokens["admin"]))
        assert r.status_code == 404


# ── TestXhsAccountIsolation ───────────────────────────────────────────────────

class TestXhsAccountIsolation:

    @pytest.fixture(autouse=True)
    def _accounts(self, client, tokens, request):
        # Append test name to avoid UNIQUE constraint collisions across tests
        suffix = request.node.name[:20]
        self.acc_a = _create_account(client, tokens["admin"], f"隔离A-{suffix}")
        self.acc_b = _create_account(client, tokens["admin"], f"隔离B-{suffix}")

    def test_upload_to_a_not_visible_in_b(self, client, tokens):
        _upload(client, tokens["analyst"], self.acc_a["id"],
                title="A专属笔记", date_str="2026年05月01日00时00分00秒")
        r = client.get(
            "/media/xhs/posts",
            params={"account_id": self.acc_b["id"]},
            headers=_auth(tokens["analyst"]),
        )
        titles = [p["title"] for p in r.json()]
        assert "A专属笔记" not in titles

    def test_same_title_different_accounts_are_separate_rows(self, client, tokens):
        shared_title = "同名笔记"
        shared_date = "2026年05月10日00时00分00秒"
        _upload(client, tokens["analyst"], self.acc_a["id"], shared_title, shared_date)
        _upload(client, tokens["analyst"], self.acc_b["id"], shared_title, shared_date)

        r_a = client.get("/media/xhs/posts",
                         params={"account_id": self.acc_a["id"]},
                         headers=_auth(tokens["analyst"]))
        r_b = client.get("/media/xhs/posts",
                         params={"account_id": self.acc_b["id"]},
                         headers=_auth(tokens["analyst"]))

        titles_a = [p["title"] for p in r_a.json()]
        titles_b = [p["title"] for p in r_b.json()]
        assert shared_title in titles_a
        assert shared_title in titles_b

    def test_account_id_filter_returns_only_that_account(self, client, tokens):
        _upload(client, tokens["analyst"], self.acc_a["id"],
                "仅A笔记", "2026年05月15日00时00分00秒")
        r = client.get(
            "/media/xhs/posts",
            params={"account_id": self.acc_a["id"]},
            headers=_auth(tokens["analyst"]),
        )
        for post in r.json():
            assert post["account_id"] == self.acc_a["id"]

    def test_delete_account_cascades_posts(self, client, tokens):
        _upload(client, tokens["analyst"], self.acc_a["id"],
                "将被删除", "2026年05月20日00时00分00秒")
        client.delete(
            f"/media/xhs/accounts/{self.acc_a['id']}",
            headers=_auth(tokens["admin"]),
        )
        r = client.get(
            "/media/xhs/posts",
            params={"account_id": self.acc_a["id"]},
            headers=_auth(tokens["analyst"]),
        )
        assert r.json() == []


# ── TestXhsUploadRequiresAccount ─────────────────────────────────────────────

class TestXhsUploadRequiresAccount:

    def test_upload_without_account_id_returns_422(self, client, tokens):
        r = client.post(
            "/media/xhs/upload",
            files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(),
                            "application/octet-stream")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 422

    def test_upload_with_nonexistent_account_returns_404(self, client, tokens):
        r = client.post(
            "/media/xhs/upload",
            data={"account_id": 999999},
            files={"file": ("xhs.xlsx", _make_xhs_xlsx_bytes(),
                            "application/octet-stream")},
            headers=_auth(tokens["analyst"]),
        )
        assert r.status_code == 404

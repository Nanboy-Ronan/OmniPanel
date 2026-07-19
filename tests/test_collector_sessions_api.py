"""Tests for /admin/collector/sessions and /admin/collector/runs.

Modeled on tests/test_xhs_accounts.py: reuses the `client`/`tokens` fixtures
from test_api_endpoints.py (real test DB via TestClient), and overrides
settings.collector_dir to a pytest tmp_path so session files never touch the
real collector directory.
"""
from __future__ import annotations

import io
import json
import os
import stat

import pytest

from test_api_endpoints import client, tokens  # noqa: F401


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _collector_dir(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "collector_dir", str(tmp_path), raising=False)
    yield tmp_path


def _session_bytes(cookies: list | None = ("anything",)) -> bytes:
    body = {"cookies": list(cookies) if cookies is not None else "not-a-list", "origins": []}
    return json.dumps(body).encode("utf-8")


def _create_xhs_account(client, admin_token: str, name: str = "采集测试号") -> dict:
    r = client.post("/media/xhs/accounts", json={"name": name}, headers=_auth(admin_token))
    assert r.status_code == 201, r.text
    return r.json()


class TestUploadSession:
    def test_admin_can_upload_zhihu_session(self, client, tokens):
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 201, r.text
        assert r.json() == {"platform": "zhihu", "account_id": None, "status": "saved"}

    def test_non_admin_forbidden(self, client, tokens):
        for role in ("analyst", "viewer"):
            r = client.post(
                "/admin/collector/sessions",
                data={"platform": "zhihu"},
                files={"file": ("zhihu.json", _session_bytes(), "application/json")},
                headers=_auth(tokens[role]),
            )
            assert r.status_code == 403, f"{role} should get 403"

    def test_unauthenticated_forbidden(self, client):
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(), "application/json")},
        )
        assert r.status_code == 401

    def test_xhs_requires_account_id(self, client, tokens):
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "xhs"},
            files={"file": ("xhs.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 422

    def test_xhs_rejects_unknown_account_id(self, client, tokens):
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "xhs", "account_id": 999999},
            files={"file": ("xhs.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 404

    def test_xhs_valid_account_saves(self, client, tokens, _collector_dir):
        acc = _create_xhs_account(client, tokens["admin"])
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "xhs", "account_id": acc["id"]},
            files={"file": ("xhs.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 201, r.text
        saved = _collector_dir / "sessions" / f"xhs_{acc['id']}.json"
        assert saved.exists()

    def test_saved_file_is_mode_0600(self, client, tokens, _collector_dir):
        client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        saved = _collector_dir / "sessions" / "zhihu.json"
        mode = stat.S_IMODE(os.stat(saved).st_mode)
        assert mode == 0o600

    def test_rejects_non_json(self, client, tokens):
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", b"not json at all", "application/json")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 400

    def test_rejects_missing_cookies_key(self, client, tokens):
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", json.dumps({"origins": []}).encode(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 400

    def test_rejects_cookies_not_a_list(self, client, tokens):
        r = client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(cookies=None), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 400

    def test_reupload_overwrites(self, client, tokens, _collector_dir):
        client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(["a"]), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(["a", "b"]), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        saved = _collector_dir / "sessions" / "zhihu.json"
        assert json.loads(saved.read_text())["cookies"] == ["a", "b"]


class TestListAndDeleteSessions:
    def test_list_empty(self, client, tokens):
        r = client.get("/admin/collector/sessions", headers=_auth(tokens["admin"]))
        assert r.status_code == 200
        assert r.json() == []

    def test_list_shows_uploaded_zhihu_session(self, client, tokens):
        client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        r = client.get("/admin/collector/sessions", headers=_auth(tokens["admin"]))
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["platform"] == "zhihu"
        assert rows[0]["account_id"] is None
        assert rows[0]["last_run_status"] is None

    def test_list_includes_account_name_for_xhs(self, client, tokens):
        acc = _create_xhs_account(client, tokens["admin"], "带名字的号")
        client.post(
            "/admin/collector/sessions",
            data={"platform": "xhs", "account_id": acc["id"]},
            files={"file": ("xhs.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        r = client.get("/admin/collector/sessions", headers=_auth(tokens["admin"]))
        rows = r.json()
        assert rows[0]["account_name"] == "带名字的号"

    def test_delete_removes_session(self, client, tokens):
        client.post(
            "/admin/collector/sessions",
            data={"platform": "zhihu"},
            files={"file": ("zhihu.json", _session_bytes(), "application/json")},
            headers=_auth(tokens["admin"]),
        )
        r = client.delete(
            "/admin/collector/sessions",
            params={"platform": "zhihu"},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 204
        r2 = client.get("/admin/collector/sessions", headers=_auth(tokens["admin"]))
        assert r2.json() == []

    def test_delete_nonexistent_returns_404(self, client, tokens):
        r = client.delete(
            "/admin/collector/sessions",
            params={"platform": "xhs", "account_id": 12345},
            headers=_auth(tokens["admin"]),
        )
        assert r.status_code == 404

    def test_non_admin_cannot_list(self, client, tokens):
        r = client.get("/admin/collector/sessions", headers=_auth(tokens["viewer"]))
        assert r.status_code == 403


class TestCollectorRunsEndpoint:
    def test_empty_runs_list(self, client, tokens):
        r = client.get("/admin/collector/runs", headers=_auth(tokens["admin"]))
        assert r.status_code == 200
        assert r.json() == []

    def test_non_admin_cannot_list_runs(self, client, tokens):
        r = client.get("/admin/collector/runs", headers=_auth(tokens["analyst"]))
        assert r.status_code == 403

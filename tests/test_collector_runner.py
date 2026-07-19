"""Tests for app/collector/runner.py — orchestration logic only.

Every network/browser/DB dependency is injected as a fake, so these run
without Postgres, Chromium, or the real portals.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.collector import runner
from app.collector.errors import DownloadTimeoutError, SessionExpiredError


@dataclasses.dataclass
class _FakeSettings:
    collector_enabled: bool = True
    collector_xhs_enabled: bool = True
    collector_zhihu_enabled: bool = True
    collector_api_url: str = "http://fake"
    collector_service_email: str | None = "svc@example.com"
    collector_service_password: str | None = "pw"
    # Default = no retry (matches pre-retry test behavior exactly, no sleeping).
    # Retry-specific tests override this and monkeypatch runner._sleep.
    collector_collect_retries: int = 1
    collector_retry_delay_seconds: int = 0


class _Resp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeAPI:
    def __init__(self, xhs_accounts=None, login_ok=True, upload_status=200, upload_body=None):
        self._xhs_accounts = xhs_accounts if xhs_accounts is not None else [{"id": 1, "is_active": True}]
        self._login_ok = login_ok
        self._upload_status = upload_status
        self._upload_body = upload_body if upload_body is not None else {"total": 1, "upserted": 1}
        self.uploads = []

    def login(self, email, password):
        return _Resp(200 if self._login_ok else 401, text="unauthorized")

    def xhs_accounts(self):
        return _Resp(200, self._xhs_accounts)

    def upload_xhs(self, data, filename, account_id):
        self.uploads.append(("xhs", account_id, filename))
        return _Resp(self._upload_status, self._upload_body, text="upload error")

    def upload_zhihu(self, data, filename, content_type):
        self.uploads.append(("zhihu", content_type, filename))
        return _Resp(self._upload_status, self._upload_body, text="upload error")


@pytest.fixture(autouse=True)
def _fake_bookkeeping(monkeypatch):
    """Replace DB-backed start_run/finish_run with in-memory recorders."""
    calls = {"started": [], "finished": []}

    def _start_run(platform, *, account_id=None, content_type=None, triggered_by="schedule"):
        run_id = len(calls["started"]) + 1
        calls["started"].append({"id": run_id, "platform": platform, "account_id": account_id,
                                  "content_type": content_type, "triggered_by": triggered_by})
        return run_id

    def _finish_run(run_id, status, *, rows_upserted=0, filename=None, error_message=None):
        calls["finished"].append({"id": run_id, "status": status, "rows_upserted": rows_upserted,
                                   "filename": filename, "error_message": error_message})

    monkeypatch.setattr(runner, "start_run", _start_run)
    monkeypatch.setattr(runner, "finish_run", _finish_run)
    return calls


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(runner, "send_wecom_alert", lambda text: sent.append(text) or True)
    return sent


@pytest.fixture
def fake_sleep(monkeypatch):
    """Short-circuit runner._sleep so retry-delay tests don't really sleep."""
    calls = []
    monkeypatch.setattr(runner, "_sleep", lambda seconds: calls.append(seconds))
    return calls


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """Point session_path() at tmp_path so tests control which files 'exist'."""
    def _session_path(platform, account_id):
        if platform == "xhs":
            return tmp_path / f"xhs_{account_id}.json"
        return tmp_path / "zhihu.json"

    monkeypatch.setattr(runner, "session_path", _session_path)
    return tmp_path


def _touch(path):
    path.write_text("{}")


class TestRunCollectDisabled:
    def test_disabled_exits_zero_without_touching_api(self, sessions, _fake_bookkeeping):
        settings = _FakeSettings(collector_enabled=False)
        rc = runner.run_collect(settings=settings, api_client=_FakeAPI())
        assert rc == 0
        assert _fake_bookkeeping["started"] == []


class TestRunCollectSuccess:
    def test_success_records_run_and_rows(self, sessions, _fake_bookkeeping):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False)
        api = _FakeAPI(xhs_accounts=[{"id": 1, "is_active": True}])

        def fake_collect_xhs(storage_path, headless=None):
            return b"filebytes", "export.xlsx"

        rc = runner.run_collect(
            settings=settings, api_client=api,
            collect_fns={"xhs": fake_collect_xhs, "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 0
        assert api.uploads == [("xhs", 1, "export.xlsx")]
        finished = _fake_bookkeeping["finished"]
        assert len(finished) == 1
        assert finished[0]["status"] == "success"
        assert finished[0]["rows_upserted"] == 1

    def test_dry_run_skips_upload(self, sessions, _fake_bookkeeping):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False)
        api = _FakeAPI()
        rc = runner.run_collect(
            settings=settings, api_client=api, dry_run=True,
            collect_fns={"xhs": lambda p, headless=None: (b"x", "f.xlsx"), "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 0
        assert api.uploads == []


class TestRunCollectFailureClassification:
    def test_session_expired_alerts_and_records_status(self, sessions, _fake_bookkeeping, alerts):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False)
        api = _FakeAPI()

        def raising(storage_path, headless=None):
            raise SessionExpiredError("expired")

        rc = runner.run_collect(
            settings=settings, api_client=api,
            collect_fns={"xhs": raising, "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 1
        assert _fake_bookkeeping["finished"][0]["status"] == "session_expired"
        assert len(alerts) == 1
        assert "expired" in alerts[0] or "登录态" in alerts[0]

    def test_download_timeout_records_download_failed(self, sessions, _fake_bookkeeping, alerts):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False)

        def raising(storage_path, headless=None):
            raise DownloadTimeoutError("timed out")

        rc = runner.run_collect(
            settings=settings, api_client=_FakeAPI(),
            collect_fns={"xhs": raising, "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 1
        assert _fake_bookkeeping["finished"][0]["status"] == "download_failed"

    def test_upload_rejected_records_upload_failed(self, sessions, _fake_bookkeeping, alerts):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False)
        api = _FakeAPI(upload_status=500)

        rc = runner.run_collect(
            settings=settings, api_client=api,
            collect_fns={"xhs": lambda p, headless=None: (b"x", "f.xlsx"), "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 1
        assert _fake_bookkeeping["finished"][0]["status"] == "upload_failed"

    def test_missing_session_file_is_a_failure_without_a_run_row(self, sessions, _fake_bookkeeping, alerts):
        # No file touched at sessions / "xhs_1.json"
        settings = _FakeSettings(collector_zhihu_enabled=False)
        rc = runner.run_collect(settings=settings, api_client=_FakeAPI())
        assert rc == 1
        assert _fake_bookkeeping["started"] == []  # never started a run for a target with no session
        assert len(alerts) == 1

    def test_one_failure_does_not_abort_other_targets(self, sessions, _fake_bookkeeping, alerts):
        _touch(sessions / "xhs_1.json")
        _touch(sessions / "xhs_2.json")
        settings = _FakeSettings(collector_zhihu_enabled=False)
        api = _FakeAPI(xhs_accounts=[{"id": 1, "is_active": True}, {"id": 2, "is_active": True}])

        def collect_by_account(storage_path, headless=None):
            if "xhs_1" in str(storage_path):
                raise SessionExpiredError("expired")
            return b"ok", "f.xlsx"

        rc = runner.run_collect(
            settings=settings, api_client=api,
            collect_fns={"xhs": collect_by_account, "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 1
        statuses = {f["status"] for f in _fake_bookkeeping["finished"]}
        assert statuses == {"session_expired", "success"}

    def test_login_failure_alerts_and_returns_nonzero(self, sessions, _fake_bookkeeping, alerts, monkeypatch):
        import app.ui.api_client as api_client_mod

        class _FailingAPIClient:
            def __init__(self, base_url=None):
                pass

            def login(self, email, password):
                return _Resp(401, text="unauthorized")

        monkeypatch.setattr(api_client_mod, "APIClient", _FailingAPIClient)
        settings = _FakeSettings()
        rc = runner.run_collect(settings=settings, api_client=None)
        assert rc == 1
        assert len(alerts) == 1
        assert _fake_bookkeeping["started"] == []

    def test_inactive_xhs_account_is_skipped(self, sessions, _fake_bookkeeping):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False)
        api = _FakeAPI(xhs_accounts=[{"id": 1, "is_active": False}])
        rc = runner.run_collect(
            settings=settings, api_client=api,
            collect_fns={"xhs": lambda p, headless=None: (b"x", "f"), "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 0
        assert _fake_bookkeeping["started"] == []


class TestRunCollectRetry:
    def test_transient_timeout_then_success_no_alert(self, sessions, _fake_bookkeeping, alerts, fake_sleep):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False, collector_collect_retries=3,
                                  collector_retry_delay_seconds=60)
        api = _FakeAPI()
        attempts = {"n": 0}

        def flaky(storage_path, headless=None):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise DownloadTimeoutError("transient")
            return b"ok", "f.xlsx"

        rc = runner.run_collect(
            settings=settings, api_client=api,
            collect_fns={"xhs": flaky, "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 0
        assert attempts["n"] == 3
        assert fake_sleep == [60, 60]  # slept before the 2nd and 3rd attempts only
        assert alerts == []
        assert _fake_bookkeeping["finished"][0]["status"] == "success"

    def test_exhausts_retries_then_download_failed(self, sessions, _fake_bookkeeping, alerts, fake_sleep):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False, collector_collect_retries=3,
                                  collector_retry_delay_seconds=60)
        attempts = {"n": 0}

        def always_timing_out(storage_path, headless=None):
            attempts["n"] += 1
            raise DownloadTimeoutError("still timing out")

        rc = runner.run_collect(
            settings=settings, api_client=_FakeAPI(),
            collect_fns={"xhs": always_timing_out, "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 1
        assert attempts["n"] == 3
        assert fake_sleep == [60, 60]
        assert len(alerts) == 1
        assert _fake_bookkeeping["finished"][0]["status"] == "download_failed"

    def test_session_expired_is_never_retried(self, sessions, _fake_bookkeeping, alerts, fake_sleep):
        _touch(sessions / "xhs_1.json")
        settings = _FakeSettings(collector_zhihu_enabled=False, collector_collect_retries=3,
                                  collector_retry_delay_seconds=60)
        attempts = {"n": 0}

        def expired(storage_path, headless=None):
            attempts["n"] += 1
            raise SessionExpiredError("dead session")

        rc = runner.run_collect(
            settings=settings, api_client=_FakeAPI(),
            collect_fns={"xhs": expired, "zhihu": lambda *a, **kw: (b"", "x")},
        )
        assert rc == 1
        assert attempts["n"] == 1  # no retry on a dead session
        assert fake_sleep == []
        assert _fake_bookkeeping["finished"][0]["status"] == "session_expired"


class TestBuildTargets:
    def test_zhihu_produces_article_and_qa_targets(self, sessions):
        settings = _FakeSettings(collector_xhs_enabled=False)
        targets = runner.build_targets(_FakeAPI(), settings)
        content_types = sorted(t.content_type for t in targets)
        assert content_types == ["article", "qa"]

    def test_xhs_produces_one_target_per_active_account(self, sessions):
        settings = _FakeSettings(collector_zhihu_enabled=False)
        api = _FakeAPI(xhs_accounts=[
            {"id": 1, "is_active": True},
            {"id": 2, "is_active": False},
            {"id": 3, "is_active": True},
        ])
        targets = runner.build_targets(api, settings)
        assert sorted(t.account_id for t in targets) == [1, 3]

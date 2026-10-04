"""Session bootstrap and shareable-filter seeding.

This logic ran inline in dashboard.py's script body, so the only way to reach
it was to run the whole app. It is now a pure function over a plain dict.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.ui import session

TODAY = date(2026, 10, 3)
THIRTY_AGO = date(2026, 9, 3)


def test_module_imports_without_a_streamlit_script_context():
    import importlib

    importlib.reload(session)


def test_seeds_every_shareable_filter():
    state: dict = {}
    session.seed_filters(state, {}, today=TODAY)
    assert set(state) == session.SEEDED_FILTER_KEYS


def test_date_filters_default_to_a_trailing_thirty_day_window():
    state: dict = {}
    session.seed_filters(state, {}, today=TODAY)
    assert state["analysis_start"] == THIRTY_AGO
    assert state["analysis_end"] == TODAY
    assert state["cust_start"] == THIRTY_AGO
    assert state["cust_end"] == TODAY


def test_a_shared_link_overrides_the_default():
    state: dict = {}
    session.seed_filters(
        state,
        {"analysis_start": "2026-01-15", "analysis_platform": "jd", "analysis_mode": "新老客户"},
        today=TODAY,
    )
    assert state["analysis_start"] == date(2026, 1, 15)
    assert state["analysis_platform"] == "jd"
    assert state["analysis_mode"] == "新老客户"


@pytest.mark.parametrize(
    "params",
    [
        {"analysis_start": "not-a-date"},
        {"analysis_start": "2026-13-45"},
        {"analysis_platform": "'; DROP TABLE orders; --"},
        {"analysis_platform": "taobao"},
        {"analysis_mode": "任意模式"},
    ],
)
def test_a_bad_query_parameter_falls_back_to_the_default(params):
    state: dict = {}
    session.seed_filters(state, params, today=TODAY)
    key = next(iter(params))
    expected = {"analysis_start": THIRTY_AGO, "analysis_platform": "全部", "analysis_mode": "概览"}[key]
    assert state[key] == expected


def test_an_existing_value_is_never_overwritten():
    """A filter the user already changed must survive the next rerun."""
    state = {"analysis_platform": "tmall"}
    session.seed_filters(state, {"analysis_platform": "jd"}, today=TODAY)
    assert state["analysis_platform"] == "tmall"


def test_bootstrap_sets_identity_defaults():
    state: dict = {}
    session.bootstrap_state(state, {})
    assert state["token"] is None
    assert state["user_role"] == ""
    assert state["is_admin"] is False
    assert state["page"] == "KPI 看板"
    assert state["_active_section"] == "ecommerce"


def test_bootstrap_honours_a_page_in_the_url():
    state: dict = {}
    session.bootstrap_state(state, {"page": "周报"})
    assert state["page"] == "周报"


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class _Client:
    def __init__(self, resp):
        self._resp, self.calls = resp, 0

    def me(self):
        self.calls += 1
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


def test_identity_is_read_once_and_marks_admins():
    state = {"token": "t", "user_role": ""}
    client = _Client(_Resp({"display_name": "陈经理", "role": "admin"}))
    session.refresh_identity(state, client)
    assert state["display_name"] == "陈经理"
    assert state["is_admin"] is True

    session.refresh_identity(state, client)
    assert client.calls == 1, "identity should not be re-fetched on every rerun"


def test_identity_lookup_failure_leaves_the_session_usable():
    state = {"token": "t", "user_role": ""}
    session.refresh_identity(state, _Client(RuntimeError("API down")))
    assert state["is_admin"] is False


def test_unknown_role_in_the_response_is_treated_as_viewer():
    state = {"token": "t", "user_role": ""}
    session.refresh_identity(state, _Client(_Resp({"display_name": "X"})))
    assert state["user_role"] == "viewer"
    assert state["is_admin"] is False


def test_identity_is_not_fetched_before_login():
    state = {"token": None, "user_role": ""}
    client = _Client(_Resp({"role": "admin"}))
    session.refresh_identity(state, client)
    assert client.calls == 0


def test_attach_client_reuses_one_client_and_tracks_the_token():
    made = []

    def factory(token=None):
        made.append(token)
        return type("C", (), {"token": token})()

    state = {"token": "first"}
    client = session.attach_client(state, factory)
    state["token"] = "second"
    again = session.attach_client(state, factory)
    assert again is client, "a new client per rerun would drop the HTTP session"
    assert again.token == "second"
    assert made == ["first"]

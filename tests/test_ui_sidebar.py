"""Sidebar logic that used to be inline in dashboard.py's script body.

resolve_current_page is the guard that decides what a user actually lands on
when the URL names a page their role cannot open — previously a bare
`if _current not in _all_pages` buried between two st.sidebar calls.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.ui import registry, sidebar


def test_module_imports_without_a_streamlit_script_context():
    import importlib

    importlib.reload(sidebar)


# ── resolve_current_page ────────────────────────────────────────────────────

def test_a_valid_page_is_kept_and_its_section_selected():
    state = {"page": "周报", "user_role": "admin", "_active_section": registry.ECOMMERCE}
    pages = registry.all_pages("admin", True)
    assert sidebar.resolve_current_page(state, pages) == "周报"
    assert state["_active_section"] == registry.MEDIA


def test_a_viewer_pointed_at_an_admin_page_lands_on_their_default():
    """A shared ?page=操作日志 link must not leave a viewer on a blank shell."""
    state = {"page": "操作日志", "user_role": "viewer", "_active_section": registry.ADMIN}
    pages = registry.all_pages("viewer", False)

    assert sidebar.resolve_current_page(state, pages) == "数据浏览"
    assert state["page"] == "数据浏览"
    assert state["_active_section"] == registry.ECOMMERCE


def test_a_viewer_pointed_at_a_media_page_lands_on_their_default():
    state = {"page": "小红书数据", "user_role": "viewer", "_active_section": registry.MEDIA}
    assert sidebar.resolve_current_page(state, registry.all_pages("viewer", False)) == "数据浏览"


def test_an_unknown_page_falls_back_for_an_analyst():
    state = {"page": "不存在的页面", "user_role": "analyst", "_active_section": registry.MEDIA}
    assert sidebar.resolve_current_page(state, registry.all_pages("analyst", False)) == "KPI 看板"
    assert state["_active_section"] == registry.ECOMMERCE


def test_an_analyst_pointed_at_an_admin_page_falls_back():
    state = {"page": "数据库状态", "user_role": "analyst", "_active_section": registry.ADMIN}
    assert sidebar.resolve_current_page(state, registry.all_pages("analyst", False)) == "KPI 看板"


# ── section highlighting ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "active,section,expected",
    [
        (registry.MEDIA, registry.MEDIA, "section-label is-active"),
        (registry.MEDIA, registry.ECOMMERCE, "section-label"),
        (None, registry.ADMIN, "section-label"),
    ],
)
def test_only_the_active_section_label_gets_the_active_class(active, section, expected):
    assert sidebar._section_class({"_active_section": active}, section) == expected


# ── saved views ─────────────────────────────────────────────────────────────

def test_applying_a_saved_view_sets_filters_and_navigates(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(sidebar.st, "session_state", state, raising=False)

    sidebar._apply_saved_view(
        {"start_date": "2026-01-01", "end_date": "2026-03-31", "platform": "jd", "mode": "新老客户"}
    )
    assert state["analysis_start"] == dt.date(2026, 1, 1)
    assert state["analysis_end"] == dt.date(2026, 3, 31)
    assert state["analysis_platform"] == "jd"
    assert state["analysis_mode"] == "新老客户"
    assert state["page"] == "数据分析"
    assert state["_active_section"] == registry.ECOMMERCE


def test_a_saved_view_with_a_corrupt_date_still_opens(monkeypatch):
    """A bad stored filter must not take the sidebar down with it."""
    state: dict = {}
    monkeypatch.setattr(sidebar.st, "session_state", state, raising=False)

    sidebar._apply_saved_view({"start_date": "not-a-date", "platform": "tmall"})
    assert "analysis_start" not in state
    assert state["analysis_platform"] == "tmall"
    assert state["page"] == "数据分析"


def test_an_empty_saved_view_still_navigates(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(sidebar.st, "session_state", state, raising=False)
    sidebar._apply_saved_view({})
    assert state["page"] == "数据分析"


# ── nav callbacks ───────────────────────────────────────────────────────────

def test_nav_callback_moves_page_and_section_together(monkeypatch):
    state = {"_nav_media": "周报"}
    monkeypatch.setattr(sidebar.st, "session_state", state, raising=False)

    sidebar._nav_callback(registry.MEDIA, "_nav_media")()
    assert state["page"] == "周报"
    assert state["_active_section"] == registry.MEDIA

"""Role visibility rules, now that they are a pure function.

These lived inside dashboard.py's script body as `if st.session_state.get(
"user_role") == "viewer"` rebinds, so they could only be exercised by running
the whole app. registry.pages_for is importable and side-effect free.
"""
from __future__ import annotations

import pytest

from app.ui import registry


def test_module_imports_without_a_streamlit_script_context():
    """Nothing here may call st.* at import time — the AppTest runners rely on
    only dashboard.py re-executing per run."""
    import importlib

    importlib.reload(registry)  # would warn/raise if it touched st.*


def test_admin_sees_every_page():
    ec, media, admin = registry.pages_for("admin", True)
    assert len(ec) == 9 and len(media) == 8 and len(admin) == 4
    assert len(registry.all_pages("admin", True)) == 21


def test_analyst_sees_business_pages_but_no_admin_section():
    ec, media, admin = registry.pages_for("analyst", False)
    assert len(ec) == 9 and len(media) == 8
    assert admin == {}


def test_viewer_is_limited_to_upload_browse_and_dictionary():
    ec, media, admin = registry.pages_for("viewer", False)
    assert set(ec) == {"数据上传", "数据浏览", "数据字典"}
    assert media == {} and admin == {}


def test_viewer_cannot_reach_an_admin_page_by_role_confusion():
    """is_admin must not override the viewer page restriction."""
    ec, media, admin = registry.pages_for("viewer", True)
    assert set(ec) == registry.VIEWER_ECOMMERCE
    assert media == {}


@pytest.mark.parametrize(
    "role,expected", [("viewer", "数据浏览"), ("analyst", "KPI 看板"), ("admin", "KPI 看板")]
)
def test_default_page_is_one_the_role_can_actually_open(role, expected):
    assert registry.default_page(role) == expected
    assert expected in registry.all_pages(role, role == "admin")


@pytest.mark.parametrize(
    "page,section",
    [
        ("KPI 看板", registry.ECOMMERCE),
        ("周报", registry.MEDIA),
        ("操作日志", registry.ADMIN),
        ("不存在的页面", None),
    ],
)
def test_section_of_locates_a_page_for_direct_links(page, section):
    assert registry.section_of(page) == section


def test_pages_for_returns_copies_so_callers_cannot_mutate_the_registry():
    ec, _, _ = registry.pages_for("admin", True)
    ec.pop("KPI 看板")
    assert "KPI 看板" in registry.ECOMMERCE_PAGES


def test_every_page_label_is_unique_across_sections():
    labels = [*registry.ECOMMERCE_PAGES, *registry.MEDIA_PAGES, *registry.ADMIN_PAGES]
    assert len(labels) == len(set(labels)), "a duplicate label would shadow a page"

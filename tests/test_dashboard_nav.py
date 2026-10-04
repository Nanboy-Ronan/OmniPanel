"""Navigation invariants for the three-section sidebar.

dashboard.py used to inject per-render CSS keyed on `stRadio:nth-of-type(n)` to
blank out a second highlight it believed the inactive sections would show. They
do not: a section whose page is not current renders with index=None, Streamlit
re-keys the radio when index changes, and the old choice is dropped. The CSS was
matching nothing and broke as soon as a section was added, removed or reordered
(viewers see no 自媒体 group at all, which already shifted every nth-of-type).

These tests hold the invariant the deletion rests on.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("API_URL", "http://127.0.0.1:9")
os.environ.setdefault("API_TIMEOUT", "1")

from streamlit.testing.v1 import AppTest  # noqa: E402

RUNNER = "tests/run_dashboard_stubbed.py"


def _admin_app() -> AppTest:
    at = AppTest.from_file(RUNNER)
    at.session_state["token"] = "stub-token"
    at.session_state["user_role"] = "admin"
    at.session_state["is_admin"] = True
    at.session_state["page"] = "KPI 看板"
    at.run(timeout=60)
    return at


def _checked(at: AppTest) -> dict[str, object]:
    out = {}
    for key in ("_nav_ec", "_nav_media", "_nav_admin"):
        try:
            out[key] = at.session_state[key]
        except Exception:
            out[key] = None
    return out


def test_admin_sees_all_three_nav_sections():
    at = _admin_app()
    assert not at.exception, [e.value for e in at.exception]
    assert [r.key for r in at.radio] == ["_nav_ec", "_nav_media", "_nav_admin"]


@pytest.mark.parametrize(
    "widget,page,expected_active",
    [
        ("_nav_ec", "数据浏览", "_nav_ec"),
        ("_nav_media", "周报", "_nav_media"),
        ("_nav_admin", "操作日志", "_nav_admin"),
    ],
)
def test_only_the_active_section_has_a_checked_item(widget, page, expected_active):
    """No stale highlight survives a jump between sections."""
    at = _admin_app()
    at.radio(key=widget).set_value(page).run(timeout=60)

    assert at.session_state["page"] == page
    checked = _checked(at)
    assert checked[expected_active] == page
    stale = {k: v for k, v in checked.items() if k != expected_active and v is not None}
    assert not stale, f"navigating to {page} left a stale selection: {stale}"


def test_navigating_between_sections_repeatedly_keeps_one_selection():
    at = _admin_app()
    for widget, page in [
        ("_nav_ec", "数据浏览"),
        ("_nav_media", "周报"),
        ("_nav_ec", "数据字典"),
        ("_nav_admin", "操作日志"),
        ("_nav_media", "小红书数据"),
    ]:
        at.radio(key=widget).set_value(page).run(timeout=60)
        assert at.session_state["page"] == page
        live = [k for k, v in _checked(at).items() if v is not None]
        assert live == [widget], f"after {page}: expected only {widget} checked, got {live}"


def test_dashboard_does_not_reintroduce_nth_of_type_nav_css():
    """nth-of-type on stRadio breaks whenever a section is added or hidden."""
    from pathlib import Path

    source = Path("app/ui/dashboard.py").read_text(encoding="utf-8")
    assert "nth-of-type" not in source, (
        "nav styling must not depend on the ordinal position of a radio — "
        "viewers render a different number of sections"
    )


def test_viewer_sees_only_the_permitted_ecommerce_pages():
    """The case the nth-of-type hack mis-numbered: viewers get one section."""
    at = AppTest.from_file(RUNNER)
    at.session_state["token"] = "stub-token"
    at.session_state["user_role"] = "viewer"
    at.session_state["is_admin"] = False
    at.session_state["page"] = "数据浏览"
    at.run(timeout=60)

    assert not at.exception, [e.value for e in at.exception]
    assert [r.key for r in at.radio] == ["_nav_ec"]
    assert set(at.radio(key="_nav_ec").options) == {"数据上传", "数据浏览", "数据字典"}


def test_no_widget_sets_a_default_for_a_key_session_already_seeds():
    """Doing both makes Streamlit print a warning banner to the user.

    session.py seeds these keys up front (that is how a shared ?analysis_start=
    link takes effect), so a widget that also passes value=/index= for the same
    key triggers "created with a default value but also had its value set via
    the Session State API" above the filter bar.
    """
    import re
    from pathlib import Path

    from app.ui.session import SEEDED_FILTER_KEYS

    assert SEEDED_FILTER_KEYS, "the seeded-filter list is empty"

    offenders = []
    for path in sorted(Path("app/ui/pages").glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = re.search(r'key="([^"]+)"', line)
            if match and match.group(1) in SEEDED_FILTER_KEYS and re.search(r"\b(value|index)=", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "widget passes a default for a key session.py already seeds:\n"
        + "\n".join(offenders)
    )

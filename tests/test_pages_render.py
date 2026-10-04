"""Every dashboard page must render without raising.

Before this existed only 2 of the 21 registered pages were exercised at all,
so any change to shared styling, helpers or theming was effectively untested
against 19 of them. These tests are deliberately shallow — they assert that a
page survives a well-formed but empty API, which is the state a fresh install
or a filtered-to-nothing view is in.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("API_URL", "http://127.0.0.1:9")
os.environ.setdefault("API_TIMEOUT", "1")

from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.page_registry import PAGES  # noqa: E402

RUNNER = "tests/run_page.py"
PAGES_DIR = Path("app/ui/pages")

# Pages that still crash on an empty payload. Each entry is a real defect to
# fix, not a page to leave broken — shrink this list, never grow it.
KNOWN_EMPTY_PAYLOAD_FAILURES: dict[str, str] = {}


def _render(page: str, payloads=None, status=200, state=None) -> AppTest:
    at = AppTest.from_file(RUNNER)
    at.session_state["_render_page"] = page
    if payloads:
        at.session_state["_stub_payloads"] = payloads
    at.session_state["_stub_status"] = status
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run(timeout=30)
    return at


@pytest.mark.parametrize("page", sorted(PAGES))
def test_page_renders_against_an_empty_api(page: str):
    if page in KNOWN_EMPTY_PAYLOAD_FAILURES:
        pytest.xfail(KNOWN_EMPTY_PAYLOAD_FAILURES[page])
    at = _render(page)
    assert not at.exception, f"{page} raised: {[e.value for e in at.exception]}"


@pytest.mark.parametrize("page", sorted(PAGES))
def test_page_degrades_without_crashing_when_the_api_is_down(page: str):
    """A 503 from every endpoint must surface as a message, not a traceback."""
    if page in KNOWN_EMPTY_PAYLOAD_FAILURES:
        pytest.xfail(KNOWN_EMPTY_PAYLOAD_FAILURES[page])
    at = _render(page, status=503)
    assert not at.exception, f"{page} raised on 503: {[e.value for e in at.exception]}"


def test_registry_covers_every_page_module():
    """Every page_* entry point in app/ui/pages must be wired into the nav.

    This replaces a test that string-parsed dashboard.py for its page dicts —
    the registry is importable now, so a page that exists but was never
    registered shows up here instead of silently being unreachable.
    """
    import importlib

    registered = {fn.__name__ for fn in PAGES.values()}
    defined = set()
    for path in sorted(PAGES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = importlib.import_module(f"app.ui.pages.{path.stem}")
        defined |= {n for n in vars(module) if n.startswith("page_") and callable(getattr(module, n))}

    unreachable = defined - registered
    assert not unreachable, f"page entry points not wired into the nav: {sorted(unreachable)}"


# Branches the default render does not reach: a page's secondary mode, lens or
# tab often has its own payload handling, and that is where unguarded indexing
# hides. Each entry is (page, session_state that selects the branch).
BRANCHES = [
    ("数据分析", {"analysis_mode": "新老客户"}),
    ("数据分析", {"analysis_mode": "概览"}),
    ("客户留存", {"cohort_lens": "逐期留存三角"}),
    ("客户留存", {"cohort_lens": "累计回归曲线"}),
]


@pytest.mark.parametrize("page,state", BRANCHES, ids=lambda v: str(v))
def test_secondary_branches_render_against_an_empty_api(page, state):
    at = _render(page, state=state)
    assert not at.exception, f"{page} {state} raised: {[e.value for e in at.exception]}"


def test_pages_do_not_index_api_payloads_unguarded():
    """A 200 with an unexpected shape must degrade, not traceback.

    Subscripting a decoded payload (`data["x"]`) raises KeyError straight into
    Streamlit's error box. Every page reads through `.get()` with a default
    instead; this keeps it that way in the branches tests cannot reach.
    """
    offenders = []
    for path in sorted(PAGES_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ".get(" in line:
                continue
            if re.search(r'(?:\br\d?\.json\(\)|\bdata|\bpayload|\braw_data)\[[\'"]', line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "unguarded payload indexing:\n" + "\n".join(offenders)

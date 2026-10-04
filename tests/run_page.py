"""AppTest entry point that renders one dashboard page against a stub API.

Driven by tests/test_pages_render.py. The page to render and any per-endpoint
payload overrides are passed in through session_state before the first run.
"""
import streamlit as st

from tests.page_registry import PAGES, StubClient

page_name = st.session_state["_render_page"]
st.session_state["client"] = StubClient(
    payloads=st.session_state.get("_stub_payloads"),
    status=st.session_state.get("_stub_status", 200),
)
st.session_state.setdefault("token", "stub-token")
st.session_state.setdefault("user_role", "admin")
st.session_state.setdefault("is_admin", True)
st.session_state.setdefault("display_name", "测试用户")

PAGES[page_name]()

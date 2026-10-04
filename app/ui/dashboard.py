# rap/app/ui/dashboard.py
"""Streamlit entry point: configure, bootstrap, authenticate, route.

This module is the only one in app/ui that runs Streamlit calls at import
time — importing it *is* rendering the app. Everything it needs lives in a
module that exposes functions (registry, session, sidebar, auth_view), so the
AppTest runners can re-execute this one file per rerun and nothing else.
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()
import streamlit as st

from app.ui.branding import CSS, ICON_PATH, SIDEBAR_BRAND_HTML

st.set_page_config(
    page_title="OmniPanel",
    layout="wide",
    page_icon=str(ICON_PATH),
    initial_sidebar_state="auto",
)
st.markdown(CSS, unsafe_allow_html=True)

from app.api_client import APIClient  # noqa: E402
from app.ui import auth_view, registry, session, sidebar  # noqa: E402
from app.ui.url_state import ALL_FILTER_KEYS, PAGE_FILTERS, shared_filter_params  # noqa: E402

# ── Bootstrap ─────────────────────────────────────────────────────────────────
session.bootstrap_state(st.session_state, st.query_params)
client = session.attach_client(st.session_state, APIClient)
session.refresh_identity(st.session_state, client)

auth_view.consume_wecom_callback(client)

# ── Auth gate ─────────────────────────────────────────────────────────────────
if not st.session_state["token"]:
    st.sidebar.markdown(SIDEBAR_BRAND_HTML, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    _, center, _ = st.columns([1, 3, 1])
    with center:
        auth_view.render_login(client)
    st.stop()

# ── Shell ─────────────────────────────────────────────────────────────────────
ecommerce_pages, media_pages, admin_pages = registry.pages_for(
    st.session_state.get("user_role", ""), bool(st.session_state.get("is_admin"))
)
current_page = sidebar.render(st.session_state, client, ecommerce_pages, media_pages, admin_pages)

# ── Route ─────────────────────────────────────────────────────────────────────
if st.query_params.get("page") != current_page:
    st.query_params["page"] = current_page

{**ecommerce_pages, **media_pages, **admin_pages}[current_page]()

# Keep the address bar in step with the filters this page actually owns, so a
# copied URL reproduces the view — and carries no credentials.
for _key in ALL_FILTER_KEYS - PAGE_FILTERS.get(current_page, {}).keys():
    st.query_params.pop(_key, None)
for _key, _value in shared_filter_params(current_page, st.session_state).items():
    if st.query_params.get(_key) != _value:
        st.query_params[_key] = _value

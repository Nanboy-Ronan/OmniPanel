# rap/app/ui/dashboard.py
from __future__ import annotations
import os
import re
import json
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
import streamlit as st
import pandas as pd
import altair as alt

from app.ui.api_client import APIClient
from app.ui._helpers import (
    _decode_jwt_payload,
    _is_mobile_or_wecom,
    _page_hero,
    _PLATFORM_LABELS,
    _relative_time,
    _wecom_redirect_uri,
    logout,
    show_api_error,
    switch_mode,
)
from app.ui.pages.kpi_overview import page_kpi_overview
from app.ui.pages.xhs_upload import page_xhs_upload
from app.ui.pages.zhihu_upload import page_zhihu_upload
from app.ui.pages.data_dictionary import page_data_dictionary
from app.ui.pages.upload import page_upload
from app.ui.pages.analysis import page_analysis
from app.ui.pages.media import page_media
from app.ui.pages.media_traffic import page_media_traffic
# from app.ui.pages.media_upload import page_media_upload  # disabled xlsx upload 2026-06-01
from app.ui.pages.content_impact import page_content_impact
from app.ui.pages.customers import page_customers
from app.ui.pages.customer_identity import page_customer_identity
from app.ui.pages.cohort_retention import page_cohort_retention
from app.ui.pages.data_browse import page_data
from app.ui.pages.sql_console import page_sql_console
from app.ui.pages.db_status import page_db_status
from app.ui.pages.user_management import page_user_management
from app.ui.pages.logs import page_logs
from app.ui.pages.collector import page_collector

st.set_page_config(
    page_title="OmniPanel",
    layout="wide",
    page_icon="▪",
    initial_sidebar_state="auto",
)

CUSTOM_CSS = "<style>" + (Path(__file__).parent / "static" / "dashboard.css").read_text() + "</style>"

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ── Session state defaults ────────────────────────────────────────────────────
if "token" not in st.session_state:
    st.session_state["token"] = None
if "signup_mode" not in st.session_state:
    st.session_state["signup_mode"] = False
if "signup_open" not in st.session_state:
    try:
        st.session_state["signup_open"] = APIClient().registration_open()
    except Exception:
        st.session_state["signup_open"] = True
if "page" not in st.session_state:
    st.session_state["page"] = "KPI 看板"
if "_active_section" not in st.session_state:
    st.session_state["_active_section"] = "ecommerce"
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False
if "display_name" not in st.session_state:
    st.session_state["display_name"] = ""
if "user_role" not in st.session_state:
    st.session_state["user_role"] = ""
if "login_method" not in st.session_state:
    st.session_state["login_method"] = ""

# Filter persistence
_today = date.today()
_30d_ago = _today - timedelta(days=30)
for _k, _v in [
    ("analysis_start", _30d_ago),
    ("analysis_end", _today),
    ("analysis_platform", "全部"),
    ("analysis_mode", "概览"),
    ("cust_start", _30d_ago),
    ("cust_end", _today),
    ("cust_min_orders", 1),
    ("cust_platform", "全部"),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

if "client" not in st.session_state:
    st.session_state["client"] = APIClient(token=st.session_state["token"])
else:
    st.session_state["client"].token = st.session_state["token"]

client = st.session_state["client"]

if st.session_state["token"]:
    try:
        r = client.list_users()
    except Exception:
        r = None
    st.session_state["is_admin"] = r is not None and r.status_code == 200

    if not st.session_state.get("display_name"):
        try:
            r_me = client.me()
            if r_me.status_code == 200:
                _me = r_me.json()
                st.session_state["display_name"] = _me.get("display_name", "")
                st.session_state["user_role"] = _me.get("role", "viewer")
        except Exception:
            pass


# ── Sidebar helpers (use module-level client) ─────────────────────────────────

def render_sidebar_status() -> None:
    try:
        r = client.upload_summary()
    except Exception:
        return
    if r.status_code != 200:
        return
    data = r.json()
    platforms_data = data.get("platforms", {})

    if not any(v.get("orders", 0) for v in platforms_data.values()):
        return

    rows_html = ""
    for pf, label in _PLATFORM_LABELS.items():
        pf_info = platforms_data.get(pf, {})
        orders = pf_info.get("orders", 0)
        age = _relative_time(pf_info.get("last_upload"))
        dot = "pf-dot" if orders else "pf-dot pf-dot-off"
        count = f"{orders:,}" if orders else "—"
        rows_html += (
            f"<div class='pf-row'>"
            f"<span class='pf-label'><span class='{dot}'></span>{label}</span>"
            f"<span class='pf-meta'>{count} · {age}</span>"
            f"</div>"
        )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        f"<div class='section-label'>Data Overview</div>{rows_html}",
        unsafe_allow_html=True,
    )


def consume_wecom_callback() -> None:
    if st.session_state.get("token"):
        return
    params = st.query_params
    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return
    with st.spinner("正在完成企业微信登录…"):
        try:
            r = client.wecom_exchange(str(code), str(state))
        except Exception as exc:
            st.query_params.clear()
            st.error(f"企业微信登录失败：{exc}")
            st.stop()
    st.query_params.clear()
    if r.status_code == 200:
        data = r.json()
        user_info = data.get("user", {})
        st.session_state["token"] = client.token
        st.session_state["display_name"] = user_info.get("name") or user_info.get("email", "").split("@")[0]
        st.session_state["user_role"] = user_info.get("role", "viewer")
        st.session_state["login_method"] = "wecom"
        st.rerun()
    show_api_error(r, "企业微信登录失败。")
    st.stop()


def auth_form():
    st.markdown(
        "<div class='login-header'>"
        "<div class='login-mark'>R</div>"
        "<div class='login-name'>OmniPanel</div>"
        "<div class='login-sub'>内部数据分析平台</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Primary: WeCom OAuth ─────────────────────────────────────
    try:
        r_url = client.wecom_authorize_url(_wecom_redirect_uri())
    except Exception:
        r_url = None
    wecom_url = None
    wecom_oauth2_url = None
    if r_url is not None and r_url.status_code == 200:
        try:
            _payload = r_url.json()
            wecom_url = _payload.get("authorize_url")
            wecom_oauth2_url = _payload.get("oauth2_url") or wecom_url
        except Exception:
            pass

    if wecom_url:
        _wecom_icon = (
            '<svg width="20" height="20" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="40" height="40" rx="8" fill="white" fill-opacity="0.2"/>'
            '<path d="M16.5 10C10.701 10 6 13.91 6 18.75c0 2.72 1.46 5.15 3.76 6.8L8.5 29l4.1-2.05A12.3 12.3 0 0016.5 27.5c.34 0 .68-.01 1.01-.04A7.46 7.46 0 0117 25.5c0-4.14 3.81-7.5 8.5-7.5.3 0 .6.01.89.03C25.45 13.48 21.37 10 16.5 10z" fill="white"/>'
            '<path d="M25.5 19c-4.14 0-7.5 2.91-7.5 6.5S21.36 32 25.5 32c1.14 0 2.22-.25 3.17-.69L32 33l-1.19-3.32C32.17 28.36 33 27 33 25.5c0-3.59-3.36-6.5-7.5-6.5z" fill="white"/>'
            '</svg>'
        )
        use_mobile = _is_mobile_or_wecom()
        btn_href = wecom_oauth2_url if use_mobile else wecom_url
        btn_label = "企业微信一键登录" if use_mobile else "企业微信扫码登录"
        st.markdown(
            f"<div class='wecom-btn-wrap'>"
            f"<a href='{btn_href}' class='wecom-btn'>"
            f"{_wecom_icon}"
            f"<span>{btn_label}</span>"
            f"</a></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='wecom-unavail'>企业微信登录暂不可用，请稍后重试或联系管理员</div>",
            unsafe_allow_html=True,
        )

    # ── Secondary: admin password login (collapsed) ──────────────
    # Temporarily commented out to force all users through Enterprise WeChat
    # QR login. Keep this block intact so password login can be restored later.
    #
    # st.markdown("<div class='login-divider'>管理员账号登录</div>", unsafe_allow_html=True)
    # with st.expander("展开管理员登录", expanded=False):
    #     signup_open = st.session_state.get("signup_open", True)
    #     if not signup_open:
    #         st.session_state["signup_mode"] = False
    #     mode = "注册" if st.session_state["signup_mode"] else "登录"
    #     with st.form("admin-" + mode):
    #         email = st.text_input("邮箱", placeholder="admin@company.com")
    #         pwd = st.text_input("密码", type="password", placeholder="••••••••")
    #         if st.form_submit_button(mode, use_container_width=True):
    #             if st.session_state["signup_mode"]:
    #                 r = client.register(email, pwd)
    #                 if r.status_code == 201:
    #                     st.success("注册成功，请登录。")
    #                     st.session_state["signup_mode"] = False
    #                 else:
    #                     show_api_error(r)
    #             else:
    #                 r = client.login(email, pwd)
    #                 if r.status_code == 200:
    #                     st.session_state["token"] = client.token
    #                     st.session_state["login_method"] = "password"
    #                     try:
    #                         r_me = client.me()
    #                         if r_me.status_code == 200:
    #                             me_data = r_me.json()
    #                             st.session_state["display_name"] = me_data.get("display_name") or email.split("@")[0]
    #                             st.session_state["user_role"] = me_data.get("role", "viewer")
    #                         else:
    #                             st.session_state["display_name"] = email.split("@")[0]
    #                     except Exception:
    #                         st.session_state["display_name"] = email.split("@")[0]
    #                     st.rerun()
    #                 else:
    #                     show_api_error(r)
    #     if signup_open:
    #         st.button(
    #             "切换到" + ("登录" if st.session_state["signup_mode"] else "注册"),
    #             on_click=switch_mode,
    #             use_container_width=True,
    #         )


# ── Page registry ─────────────────────────────────────────────────────────────
consume_wecom_callback()

ECOMMERCE_PAGES = {
    "KPI 看板":   page_kpi_overview,
    "数据上传":   page_upload,
    "数据分析":   page_analysis,
    "客户管理":   page_customers,
    "跨平台客户": page_customer_identity,
    "客户留存":   page_cohort_retention,
    "数据浏览":   page_data,
    "SQL 控制台": page_sql_console,
    "数据字典":   page_data_dictionary,
}

MEDIA_PAGES = {
    # "公众号上传": page_media_upload,  # disabled xlsx upload 2026-06-01
    "公众号流量": page_media_traffic,
    "公众号数据": page_media,
    "内容带货分析": page_content_impact,
    "小红书数据": page_xhs_upload,
    "知乎数据": page_zhihu_upload,
}

ADMIN_PAGES: dict = {}
if st.session_state.get("is_admin"):
    ADMIN_PAGES = {
        "用户管理":   page_user_management,
        "操作日志":   page_logs,
        "数据库状态": page_db_status,
        "自动采集":   page_collector,
    }

# ── Render ────────────────────────────────────────────────────────────────────
if not st.session_state["token"]:
    st.sidebar.markdown(
        "<div class='rpa-logo'>"
        "<div class='rpa-logo-mark'>R</div>"
        "<div><div class='rpa-logo-text'>OmniPanel</div>"
        "<div class='rpa-logo-sub'>Enterprise</div></div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)
    _, center, _ = st.columns([1, 3, 1])
    with center:
        auth_form()
    st.stop()

st.sidebar.markdown(
    "<div class='rpa-logo'>"
    "<div class='rpa-logo-mark'>R</div>"
    "<div><div class='rpa-logo-text'>OmniPanel</div>"
    "<div class='rpa-logo-sub'>Enterprise</div></div>"
    "</div>",
    unsafe_allow_html=True,
)

# ── User identity chip ────────────────────────────────────────────────────────
_dname = st.session_state.get("display_name") or ""
_role  = st.session_state.get("user_role") or ("admin" if st.session_state.get("is_admin") else "viewer")
_role_label = {"admin": "管理员", "analyst": "分析师", "viewer": "浏览者"}.get(_role, _role)
_avatar_char = (_dname[0].upper() if _dname else "U")
st.sidebar.markdown(
    f"<div class='user-chip'>"
    f"<div class='user-avatar'>{_avatar_char}</div>"
    f"<div>"
    f"<div class='user-name'>{_dname or '用户'}</div>"
    f"<div class='user-role role-{_role}'>{_role_label}</div>"
    f"</div></div>",
    unsafe_allow_html=True,
)

# ── Token expiry warning ──────────────────────────────────────────────────────
_token = st.session_state.get("token") or ""
if _token:
    _payload = _decode_jwt_payload(_token)
    _exp = _payload.get("exp", 0)
    _remaining = _exp - _time.time()
    if 0 < _remaining < 1800:
        _mins = int(_remaining // 60)
        st.sidebar.markdown(
            f"<div class='token-expiry-warn'>会话将在 {_mins} 分钟后过期</div>",
            unsafe_allow_html=True,
        )
        st.sidebar.button("重新登录", on_click=logout, key="relogin_btn")

render_sidebar_status()

# ── Section navigation ────────────────────────────────────────────────────────

_all_pages = {**ECOMMERCE_PAGES, **MEDIA_PAGES, **ADMIN_PAGES}
_current = st.session_state["page"]
if _current not in _all_pages:
    _current = "数据上传"
    st.session_state["page"] = _current

_active_section = st.session_state["_active_section"]

# Infer active section from current page (handles direct page-link navigation)
if _current in ECOMMERCE_PAGES:
    _active_section = "ecommerce"
elif _current in MEDIA_PAGES:
    _active_section = "media"
elif _current in ADMIN_PAGES:
    _active_section = "admin"
st.session_state["_active_section"] = _active_section


def _on_ec_nav():
    st.session_state["page"] = st.session_state["_nav_ec"]
    st.session_state["_active_section"] = "ecommerce"


def _on_media_nav():
    st.session_state["page"] = st.session_state["_nav_media"]
    st.session_state["_active_section"] = "media"


def _on_admin_nav():
    st.session_state["page"] = st.session_state["_nav_admin"]
    st.session_state["_active_section"] = "admin"


_ec_color   = "#38bdf8" if _active_section == "ecommerce" else "#94a3b8"
_media_color = "#38bdf8" if _active_section == "media"    else "#94a3b8"
_admin_color = "#38bdf8" if _active_section == "admin"    else "#94a3b8"

# Inject CSS so only the active section's radio shows the blue highlight.
# The inactive section's checked label is neutralised to a plain unselected look.
_inactive_nth = {"ecommerce": (2, 3), "media": (1, 3), "admin": (1, 2)}.get(_active_section, (2, 3))
_inactive_css = " ".join(
    f"[data-testid='stSidebar'] [data-testid='stRadio']:nth-of-type({n}) label:has(input:checked) {{"
    "background: transparent !important;"
    "color: #94a3b8 !important;"
    "font-weight: 500 !important;"
    "border-left-color: transparent !important;"
    "}"
    for n in _inactive_nth
)
st.sidebar.markdown(f"<style>{_inactive_css}</style>", unsafe_allow_html=True)

# ── 商城数据 section ──────────────────────────────────────────────────────────
st.sidebar.markdown(
    f"<div class='section-label' style='color:{_ec_color} !important;'>商城数据</div>",
    unsafe_allow_html=True,
)
_ec_keys = list(ECOMMERCE_PAGES.keys())
_ec_idx  = _ec_keys.index(_current) if _current in _ec_keys else None
st.sidebar.radio(
    "ecommerce_nav",
    _ec_keys,
    index=_ec_idx,
    key="_nav_ec",
    label_visibility="collapsed",
    on_change=_on_ec_nav,
)

# ── 自媒体 section ────────────────────────────────────────────────────────────
st.sidebar.markdown(
    f"<div class='section-label' style='color:{_media_color} !important;'>自媒体</div>",
    unsafe_allow_html=True,
)
_media_keys = list(MEDIA_PAGES.keys())
_media_idx  = _media_keys.index(_current) if _current in _media_keys else None
st.sidebar.radio(
    "media_nav",
    _media_keys,
    index=_media_idx,
    key="_nav_media",
    label_visibility="collapsed",
    on_change=_on_media_nav,
)

# ── 系统管理 section (admin only) ─────────────────────────────────────────────
if ADMIN_PAGES:
    st.sidebar.markdown(
        f"<div class='section-label' style='color:{_admin_color} !important;'>系统管理</div>",
        unsafe_allow_html=True,
    )
    _admin_keys = list(ADMIN_PAGES.keys())
    _admin_idx  = _admin_keys.index(_current) if _current in _admin_keys else None
    st.sidebar.radio(
        "admin_nav",
        _admin_keys,
        index=_admin_idx,
        key="_nav_admin",
        label_visibility="collapsed",
        on_change=_on_admin_nav,
    )

st.sidebar.markdown("---")

# ── 我的视图 (saved queries) ──────────────────────────────────────────────────
try:
    _sq_resp = client.list_saved_queries()
    _saved_views = _sq_resp.json() if _sq_resp.status_code == 200 else []
except Exception:
    _saved_views = []

if _saved_views:
    st.sidebar.markdown(
        "<div class='section-label' style='color:#94a3b8 !important;'>我的视图</div>",
        unsafe_allow_html=True,
    )
    for _sq in _saved_views:
        _sq_label = _sq["name"] + (" 🔗" if _sq.get("is_shared") else "")
        _sq_cols = st.sidebar.columns([5, 1])
        if _sq_cols[0].button(_sq_label, key=f"sq-{_sq['id']}", use_container_width=True):
            _f = _sq.get("filters_json", {})
            import datetime as _dt
            if _f.get("start_date"):
                try:
                    st.session_state["analysis_start"] = _dt.date.fromisoformat(_f["start_date"])
                except Exception:
                    pass
            if _f.get("end_date"):
                try:
                    st.session_state["analysis_end"] = _dt.date.fromisoformat(_f["end_date"])
                except Exception:
                    pass
            if _f.get("platform"):
                st.session_state["analysis_platform"] = _f["platform"]
            if _f.get("mode"):
                st.session_state["analysis_mode"] = _f["mode"]
            st.session_state["page"] = "数据分析"
            st.session_state["_active_section"] = "ecommerce"
            st.rerun()
        if _sq_cols[1].button("✕", key=f"sq-del-{_sq['id']}"):
            client.delete_saved_query(_sq["id"])
            st.rerun()
    st.sidebar.markdown("---")

st.sidebar.button("退出登录", on_click=logout)

_all_pages[_current]()

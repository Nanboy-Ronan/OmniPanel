"""Login view and the Enterprise WeChat OAuth round trip.

Both took the dashboard's module-level `client`; they now receive it, so the
module is import-safe and the callback can be tested with a stub.
"""
from __future__ import annotations

import base64
import binascii
import time as _time
from html import escape
from urllib.parse import parse_qs, urlencode, urlsplit

import streamlit as st

from app.ui._helpers import _is_mobile_or_wecom, _wecom_redirect_uri, show_api_error
from app.ui.branding import ICON_DATA_URL
from app.ui.url_state import ALL_FILTER_KEYS, PAGE_FILTERS


def consume_wecom_callback(client) -> None:
    if st.session_state.get("token"):
        return
    params = st.query_params
    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return
    expected_state = st.context.cookies.get("wecom_oauth_state")
    if not expected_state or str(state) != expected_state:
        st.session_state.pop("wecom_login_urls", None)
        for key in ("code", "state"):
            st.query_params.pop(key, None)
        st.error("登录请求已过期，请重新点击企业微信登录。")
        return
    with st.spinner("正在完成企业微信登录…"):
        try:
            r = client.wecom_exchange(str(code), str(state), state_cookie=expected_state)
        except Exception as exc:
            st.session_state.pop("wecom_login_urls", None)
            for key in ("code", "state"):
                st.query_params.pop(key, None)
            st.error(f"企业微信登录失败：{exc}")
            return
    for key in ("code", "state"):
        st.query_params.pop(key, None)
    st.session_state.pop("wecom_login_urls", None)
    if r.status_code == 200:
        data = r.json()
        user_info = data.get("user", {})
        st.session_state["token"] = client.token
        st.session_state["display_name"] = user_info.get("name") or user_info.get("email", "").split("@")[0]
        st.session_state["user_role"] = user_info.get("role", "viewer")
        return_query = st.context.cookies.get("dashboard_login_return", "")
        if return_query:
            try:
                saved_params = parse_qs(base64.urlsafe_b64decode(
                    return_query + "=" * (-len(return_query) % 4)
                ).decode())
            except (binascii.Error, ValueError, UnicodeDecodeError):
                saved_params = {}
            for key in ("page", *ALL_FILTER_KEYS):
                value = saved_params.get(key, [None])[0]
                if value and len(value) <= 50:
                    st.query_params[key] = value
            if st.query_params.get("page"):
                st.session_state["page"] = st.query_params["page"]
            st.session_state["_url_filters_loaded"] = False
        st.rerun()
    show_api_error(r, "企业微信登录失败。")
    return


def render_login(client) -> None:
    st.markdown(
        "<div class='login-header'>"
        f"<img class='login-mark' src='{ICON_DATA_URL}' alt=''>"
        "<div class='login-name'>OmniPanel</div>"
        "<div class='login-sub'>内部数据分析平台</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Browser starts OAuth directly. The state cookie remains with that browser
    # across the external redirect and Streamlit's new WebSocket session.
    redirect_uri = _wecom_redirect_uri()
    parsed = urlsplit(redirect_uri)
    status_cached = st.session_state.get("wecom_status_cache")
    if status_cached and _time.time() - status_cached[0] < 60:
        login_available = status_cached[1]
    else:
        try:
            status_response = client.wecom_status()
            login_available = status_response.status_code == 200 and status_response.json().get("enabled", False)
        except Exception:
            login_available = False
        st.session_state["wecom_status_cache"] = (_time.time(), login_available)
    if login_available and parsed.scheme in {"http", "https"} and parsed.hostname:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port == 8501:
            origin = f"{parsed.scheme}://{parsed.hostname}:8000"
        use_mobile = _is_mobile_or_wecom()
        page_name = st.query_params.get("page", st.session_state.get("page", "KPI 看板"))
        return_params = {"page": page_name}
        return_params.update({key: st.query_params[key] for key in PAGE_FILTERS.get(page_name, {})
                              if key in st.query_params})
        btn_href = f"{origin}/auth/wecom/start?{urlencode({'redirect_uri': redirect_uri, 'flow': 'mobile' if use_mobile else 'qr', 'return_query': urlencode(return_params)})}"
        _wecom_icon = (
            '<svg width="20" height="20" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="40" height="40" rx="8" fill="white" fill-opacity="0.2"/>'
            '<path d="M16.5 10C10.701 10 6 13.91 6 18.75c0 2.72 1.46 5.15 3.76 6.8L8.5 29l4.1-2.05A12.3 12.3 0 0016.5 27.5c.34 0 .68-.01 1.01-.04A7.46 7.46 0 0117 25.5c0-4.14 3.81-7.5 8.5-7.5.3 0 .6.01.89.03C25.45 13.48 21.37 10 16.5 10z" fill="white"/>'
            '<path d="M25.5 19c-4.14 0-7.5 2.91-7.5 6.5S21.36 32 25.5 32c1.14 0 2.22-.25 3.17-.69L32 33l-1.19-3.32C32.17 28.36 33 27 33 25.5c0-3.59-3.36-6.5-7.5-6.5z" fill="white"/>'
            '</svg>'
        )
        btn_label = "企业微信一键登录" if use_mobile else "企业微信扫码登录"
        st.markdown(
            f"<div class='wecom-btn-wrap'>"
            f"<a href='{escape(btn_href, quote=True)}' class='wecom-btn'>"
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

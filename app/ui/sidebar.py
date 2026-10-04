"""The sidebar: brand, identity, data status, section navigation, saved views.

Everything here is a function. dashboard.py is the only module whose body runs
Streamlit calls at import time, which is what lets the AppTest runners
re-execute a single module per rerun.
"""
from __future__ import annotations

import datetime as _dt
import time as _time
from html import escape

import streamlit as st

from app.ui import registry
from app.ui._helpers import _PLATFORM_LABELS, _relative_time, logout
from app.ui.branding import SIDEBAR_BRAND_HTML
from app.ui.session import session_expires_in

ROLE_LABELS = {"admin": "管理员", "analyst": "分析师", "viewer": "浏览者"}

#: Warn once the session has less than this long to run.
EXPIRY_WARNING_SECONDS = 1800

_STATUS_CACHE_TTL = 300
_SAVED_VIEWS_CACHE_TTL = 120


def render_brand() -> None:
    st.sidebar.markdown(SIDEBAR_BRAND_HTML, unsafe_allow_html=True)


def render_identity(state) -> None:
    name = state.get("display_name") or ""
    role = state.get("user_role") or ("admin" if state.get("is_admin") else "viewer")
    label = ROLE_LABELS.get(role, role)
    avatar = name[0].upper() if name else "U"
    st.sidebar.markdown(
        f"<div class='user-chip'>"
        f"<div class='user-avatar'>{escape(avatar)}</div>"
        f"<div>"
        f"<div class='user-name'>{escape(name or '用户')}</div>"
        f"<div class='user-role role-{escape(role, quote=True)}'>{escape(label)}</div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )


def render_expiry_warning(state) -> None:
    remaining = session_expires_in(state.get("token") or "")
    if remaining is None or not 0 < remaining < EXPIRY_WARNING_SECONDS:
        return
    st.sidebar.markdown(
        f"<div class='token-expiry-warn'>会话将在 {int(remaining // 60)} 分钟后过期</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.button("重新登录", on_click=logout, key="relogin_btn")


def render_data_overview(state, client) -> None:
    cached = state.get("sidebar_upload_summary")
    if cached and _time.time() - cached[0] < _STATUS_CACHE_TTL:
        data = cached[1]
    else:
        try:
            response = client.upload_summary()
        except Exception:
            return
        if response.status_code != 200:
            return
        data = response.json()
        state["sidebar_upload_summary"] = (_time.time(), data)

    platforms = data.get("platforms", {})
    if not any(v.get("orders", 0) for v in platforms.values()):
        return

    rows = ""
    for platform, label in _PLATFORM_LABELS.items():
        info = platforms.get(platform, {})
        orders = info.get("orders", 0)
        dot = "pf-dot" if orders else "pf-dot pf-dot-off"
        count = f"{orders:,}" if orders else "—"
        rows += (
            f"<div class='pf-row'>"
            f"<span class='pf-label'><span class='{dot}'></span>{label}</span>"
            f"<span class='pf-meta'>{count} · {_relative_time(info.get('last_upload'))}</span>"
            f"</div>"
        )

    st.sidebar.markdown("---")
    st.sidebar.markdown(f"<div class='section-label'>数据概览</div>{rows}", unsafe_allow_html=True)


_FRESHNESS_LABELS = {
    "orders": "商城订单", "wechat": "公众号", "xhs": "小红书",
    "zhihu": "知乎", "channels": "视频号", "pgy": "蒲公英",
}


def render_freshness(state, client) -> None:
    cached = state.get("data_freshness_cache")
    if cached and _time.time() - cached[0] < _STATUS_CACHE_TTL:
        data = cached[1]
    else:
        try:
            response = client.data_freshness()
            if response.status_code != 200:
                return
            data = response.json()
        except Exception:
            return
        state["data_freshness_cache"] = (_time.time(), data)

    with st.sidebar.expander("数据时间", expanded=False):
        st.caption(
            "覆盖日期是最新订单、内容发布日期或指标日期；"
            "入库时间是系统最后一次成功接收或更新记录的时间。"
        )
        for key, label in _FRESHNESS_LABELS.items():
            source = data.get(key, {})
            coverage = source.get("coverage_through") or "暂无"
            imported = source.get("last_import_at")
            imported_label = imported[:16].replace("T", " ") if imported else "暂无"
            st.caption(f"{label}：覆盖至 {coverage} · 入库 {imported_label}")


def resolve_current_page(state, pages: dict) -> str:
    """The page to render, falling back when a URL names one this role lacks."""
    current = state["page"]
    if current not in pages:
        current = registry.default_page(state.get("user_role", ""))
        state["page"] = current
    # A direct page link has to move the nav with it.
    section = registry.section_of(current)
    if section:
        state["_active_section"] = section
    return current


def _nav_callback(section: str, widget_key: str):
    def handler():
        st.session_state["page"] = st.session_state[widget_key]
        st.session_state["_active_section"] = section
    return handler


def _section_class(state, section: str) -> str:
    """Active section is marked with a class — an inline style would be stripped."""
    active = state.get("_active_section") == section
    return "section-label is-active" if active else "section-label"


def render_nav(state, current: str, ecommerce: dict, media: dict, admin: dict) -> None:
    """Three grouped radios, one per section.

    A section whose page is not the current one renders with index=None, and
    Streamlit re-keys the radio when index changes, so its previous choice is
    dropped rather than left checked. Exactly one item is ever checked across
    the three groups — test_only_the_active_section_has_a_checked_item holds
    that invariant, and it is why no CSS is needed to blank a second highlight.
    """
    for section, pages, widget_key in (
        (registry.ECOMMERCE, ecommerce, "_nav_ec"),
        (registry.MEDIA, media, "_nav_media"),
        (registry.ADMIN, admin, "_nav_admin"),
    ):
        if not pages:
            continue
        st.sidebar.markdown(
            f"<div class='{_section_class(state, section)}'>{registry.SECTION_LABELS[section]}</div>",
            unsafe_allow_html=True,
        )
        labels = list(pages)
        st.sidebar.radio(
            f"{section}_nav",
            labels,
            index=labels.index(current) if current in labels else None,
            key=widget_key,
            label_visibility="collapsed",
            on_change=_nav_callback(section, widget_key),
        )

    st.sidebar.markdown("---")


def _apply_saved_view(filters: dict) -> None:
    for field, key in (("start_date", "analysis_start"), ("end_date", "analysis_end")):
        raw = filters.get(field)
        if raw:
            try:
                st.session_state[key] = _dt.date.fromisoformat(raw)
            except (TypeError, ValueError):
                pass
    for field, key in (("platform", "analysis_platform"), ("mode", "analysis_mode")):
        if filters.get(field):
            st.session_state[key] = filters[field]
    st.session_state["page"] = "数据分析"
    st.session_state["_active_section"] = registry.ECOMMERCE


def render_saved_views(state, client) -> None:
    if state.get("user_role") == "viewer":
        return
    cached = state.get("saved_views_cache")
    if cached and _time.time() - cached[0] < _SAVED_VIEWS_CACHE_TTL:
        views = cached[1]
    else:
        try:
            response = client.list_saved_queries()
            views = response.json() if response.status_code == 200 else []
            state["saved_views_cache"] = (_time.time(), views)
        except Exception:
            views = []
    if not views:
        return

    st.sidebar.markdown("<div class='section-label'>我的视图</div>", unsafe_allow_html=True)
    for view in views:
        label = view["name"] + (" ⇗" if view.get("is_shared") else "")
        open_col, delete_col = st.sidebar.columns([5, 1])
        if open_col.button(label, key=f"sq-{view['id']}", use_container_width=True):
            _apply_saved_view(view.get("filters_json", {}))
            st.rerun()
        if delete_col.button("✕", key=f"sq-del-{view['id']}"):
            client.delete_saved_query(view["id"])
            state.pop("saved_views_cache", None)
            st.rerun()
    st.sidebar.markdown("---")


def render(state, client, ecommerce: dict, media: dict, admin: dict) -> str:
    """Draw the whole sidebar and return the page that should be rendered."""
    render_brand()
    render_identity(state)
    render_expiry_warning(state)
    render_data_overview(state, client)
    render_freshness(state, client)

    current = resolve_current_page(state, {**ecommerce, **media, **admin})
    render_nav(state, current, ecommerce, media, admin)
    render_saved_views(state, client)

    st.sidebar.button("退出登录", on_click=logout)
    st.sidebar.caption("复制当前网址可分享页面和日期、平台等筛选；链接不包含登录凭据。")
    return current

"""Shared UI utilities used by dashboard.py and all page modules."""
from __future__ import annotations
import os
import re
from datetime import datetime, timezone

import altair as alt
import streamlit as st

PALETTE = {"old": "#2563eb", "new": "#f97316"}
_PLATFORM_LABELS = {"youzan": "有赞", "jd": "京东", "tmall": "天猫"}

_PAGE_META: dict[str, tuple[str, str]] = {
    "数据上传":   ("↑",  "从有赞、京东、天猫导入订单导出文件"),
    "数据分析":   ("≋",  "营业额趋势、客户分群与平台对比"),
    "客户管理":   ("◎",  "客户档案、订单历史与地区分布"),
    "数据浏览":   ("⊞",  "浏览、筛选并导出所有订单记录"),
    "SQL 控制台": ("›_", "对实时数据库执行只读 SQL 查询"),
    "数据字典":   ("≡",  "字段定义、平台映射与实时覆盖率"),
    "公众号数据": ("W",  "微信 API 同步的文章数据与阅读趋势分析"),
    "公众号流量": ("≋",  "手动上传的文章阅读流量分析"),
    "公众号上传": ("↑",  "上传微信后台文章数据导出文件"),
    "用户管理":   ("⊕",  "创建、编辑和停用用户账号"),
    "操作日志":   ("≡",  "所有用户操作的审计记录"),
    "数据库状态": ("◈",  "数据库健康检查与危险操作区"),
}


def _styled_chart(chart):
    """Apply consistent professional styling to Altair charts."""
    return (
        chart
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelFont="Segoe UI, Helvetica Neue, sans-serif",
            titleFont="Segoe UI, Helvetica Neue, sans-serif",
            labelColor="#475569",
            titleColor="#334155",
            labelFontSize=11,
            titleFontSize=12,
            gridColor="#e2e8f0",
            gridWidth=1,
            tickColor="#cbd5e1",
            domainColor="#e2e8f0",
        )
        .configure_legend(
            labelFont="Segoe UI, Helvetica Neue, sans-serif",
            titleFont="Segoe UI, Helvetica Neue, sans-serif",
            labelColor="#1e293b",
            titleColor="#1e293b",
            labelFontSize=11,
            titleFontSize=11,
            orient="bottom",
            padding=8,
        )
    )


def _relative_time(dt_str: str | None) -> str:
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        days = delta.days
        hours = delta.seconds // 3600
        if days == 0:
            return "刚刚" if hours == 0 else f"{hours}小时前"
        if days == 1:
            return "昨天"
        return f"{days}天前"
    except Exception:
        return "—"


def _page_hero(page: str) -> None:
    icon, subtitle = _PAGE_META.get(page, ("▸", ""))
    st.markdown(
        f"<div class='page-hero'>"
        f"<div class='ph-icon'>{icon}</div>"
        f"<div><div class='ph-title'>{page}</div>"
        f"<div class='ph-sub'>{subtitle}</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _response_detail(r) -> str:
    try:
        payload = r.json()
    except Exception:
        return r.text
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, list):
        return "; ".join(str(item) for item in detail)
    return str(detail)


def show_api_error(r, fallback: str = "请求失败。") -> None:
    detail = _response_detail(r)
    if r.status_code == 401:
        st.session_state["token"] = None
        st.error("登录已过期，请重新登录。")
        st.stop()
    elif r.status_code == 403:
        st.error("您的账号没有执行该操作的权限。")
    elif r.status_code == 503:
        st.warning(detail or "分析数据尚未就绪，请先上传订单数据。")
    elif detail:
        st.error(detail)
    else:
        st.error(fallback)


def clear_cached_orders() -> None:
    st.session_state.pop("orders_df", None)


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verifying signature (for client-side exp check)."""
    try:
        import base64
        import json as _json
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        return _json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def _wecom_redirect_uri() -> str:
    return (
        os.getenv("WECOM_STREAMLIT_REDIRECT_URI")
        or os.getenv("APP_URL")
        or os.getenv("STREAMLIT_URL")
        or "http://localhost:8501"
    ).rstrip("/")


def _is_mobile_or_wecom() -> bool:
    """Server-side UA detection — avoids reliance on JS in Streamlit iframes."""
    try:
        ua = st.context.headers.get("User-Agent", "")
    except Exception:
        return False
    if "wxwork" in ua.lower():
        return True
    return bool(
        re.search(r"android|iphone|ipad|ipod|mobile", ua, re.IGNORECASE)
        and "windows phone" not in ua.lower()
    )


def logout() -> None:
    for _k in ("token", "display_name", "user_role", "login_method"):
        st.session_state[_k] = None if _k == "token" else ""
    st.session_state["page"] = "数据上传"
    st.rerun()


def switch_mode() -> None:
    st.session_state["signup_mode"] = not st.session_state["signup_mode"]

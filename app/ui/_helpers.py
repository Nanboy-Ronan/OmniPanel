"""Shared UI utilities used by dashboard.py and all page modules."""
from __future__ import annotations
import os
import re
import time
from datetime import datetime, timezone

import streamlit as st

from app.ui import theme

PALETTE = theme.PALETTE
_PLATFORM_LABELS = {"youzan": "有赞", "jd": "京东", "tmall": "天猫"}

_PAGE_SOURCE = {
    "KPI 看板": "orders", "数据分析": "orders", "数据浏览": "orders",
    "客户管理": "orders", "跨平台客户": "orders", "客户留存": "orders",
    "公众号内容分析": "wechat", "公众号流量": "wechat", "周报": "wechat",
    "小红书数据": "xhs", "蒲公英合作": "pgy", "知乎数据": "zhihu",
    "视频号数据": "channels", "内容带货分析": "wechat",
}

_PAGE_META: dict[str, tuple[str, str]] = {
    # 商城数据
    "KPI 看板":   ("◆",  "核心经营指标的日、周、月同期对比"),
    "数据上传":   ("↑",  "从有赞、京东、天猫导入订单导出文件"),
    "数据分析":   ("≋",  "营业额趋势、客户分群与平台对比"),
    "客户管理":   ("◎",  "客户档案、订单历史与地区分布"),
    "跨平台客户": ("⇄",  "跨有赞、京东、天猫识别同一客户"),
    "客户留存":   ("◷",  "按首购月份分群的留存与复购表现"),
    "数据浏览":   ("⊞",  "浏览、筛选并导出所有订单记录"),
    "SQL 控制台": ("›_", "对实时数据库执行只读 SQL 查询"),
    "数据字典":   ("≡",  "字段定义、平台映射与实时覆盖率"),
    # 自媒体
    "周报":       ("▦",  "跨渠道经营表现与内容效果周度汇总"),
    "公众号流量": ("≋",  "微信 API 同步的文章阅读流量分析"),
    "公众号内容分析": ("W",  "微信 API 同步的文章数据与阅读趋势分析"),
    "内容带货分析": ("⇗",  "公众号内容发布与订单转化的关联分析"),
    "小红书数据": ("▣",  "小红书专业号导出分析"),
    "蒲公英合作": ("✿",  "小红书蒲公英 KOL/KOC 商业合作投效分析与项目数据管理"),
    "知乎数据":   ("知", "知乎创作者后台文章与问答分析"),
    "视频号数据": ("▶",  "微信视频号内容数据自动采集与分析"),
    # 系统管理
    "用户管理":   ("⊕",  "创建、编辑和停用用户账号"),
    "操作日志":   ("≡",  "所有用户操作的审计记录"),
    "数据库状态": ("◈",  "数据库健康检查与危险操作区"),
    "自动采集":   ("⟳",  "采集任务状态、登录会话与手动触发"),
}


def _styled_chart(chart):
    """Apply consistent professional styling to Altair charts."""
    return (
        chart
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelFont=theme.FONT,
            titleFont=theme.FONT,
            labelColor=theme.INK_LABEL,
            titleColor=theme.INK_TITLE,
            labelFontSize=11,
            titleFontSize=12,
            gridColor=theme.GRID,
            gridWidth=1,
            tickColor=theme.INK_FAINT,
            domainColor=theme.GRID,
        )
        .configure_legend(
            labelFont=theme.FONT,
            titleFont=theme.FONT,
            labelColor=theme.INK_STRONG,
            titleColor=theme.INK_STRONG,
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


def _page_hero(page: str, subtitle: str | None = None) -> None:
    default_icon, default_sub = _PAGE_META.get(page, ("▸", ""))
    icon = default_icon
    sub = subtitle if subtitle is not None else default_sub
    st.markdown(
        f"<div class='page-hero'>"
        f"<div class='ph-icon'>{icon}</div>"
        f"<div><div class='ph-title'>{page}</div>"
        f"<div class='ph-sub'>{sub}</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    freshness = st.session_state.get("data_freshness_cache")
    source = _PAGE_SOURCE.get(page)
    if freshness and source:
        info = freshness[1].get(source, {})
        coverage = info.get("coverage_through") or "暂无"
        imported = info.get("last_import_at")
        imported_label = imported[:16].replace("T", " ") if imported else "暂无"
        st.caption(f"源数据覆盖至 {coverage} · 最近入库 {imported_label}（服务器时区）")


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
        clear_session()
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


def fetch_all_posts(fetch_page, **filters):
    """Load a filtered post set in stable pages for full-period analytics.

    Returns (rows, error_response). The caller never uses a partial result.
    """
    rows = []
    page_size = 1000
    while True:
        response = fetch_page(limit=page_size, offset=len(rows), **filters)
        if response.status_code != 200:
            return [], response
        page = response.json()
        rows.extend(page)
        if len(page) < page_size:
            return rows, None


def data_cache_expired(cache_key: str, *, ttl_seconds: int = 300) -> bool:
    fetched_at = st.session_state.get(f"{cache_key}_fetched_at", 0.0)
    return time.time() - fetched_at >= ttl_seconds


def mark_data_cache_fetched(cache_key: str) -> None:
    st.session_state[f"{cache_key}_fetched_at"] = time.time()


def data_cache_caption(cache_key: str) -> None:
    fetched_at = st.session_state.get(f"{cache_key}_fetched_at")
    if fetched_at:
        updated = datetime.fromtimestamp(fetched_at).strftime("%H:%M:%S")
        st.caption(f"页面取数时间 {updated}，最多缓存 5 分钟；源数据更新时间请查看上传或同步记录")


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


def clear_session() -> None:
    """Discard identity and cached data together when authentication ends."""
    client = st.session_state.get("client")
    try:
        if client is not None:
            client.close()
    finally:
        st.session_state.clear()


def logout() -> None:
    clear_session()
    st.rerun()

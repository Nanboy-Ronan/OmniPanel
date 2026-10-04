"""Which pages exist, and which of them a given role may see.

This is the one definition. It used to live inside dashboard.py's script body,
interleaved with `st.session_state` reads, so nothing could import it: three
separate tests string-parsed `ECOMMERCE_PAGES = {` out of the source to find
out what the pages were, and tests/page_registry.py kept a hand-maintained
second copy that could drift.

Import-safe on purpose — no Streamlit call happens here. Role filtering is a
pure function of (role, is_admin) so it can be tested without a script run.
"""
from __future__ import annotations

from collections.abc import Callable

from app.ui.pages.analysis import page_analysis
from app.ui.pages.channels_upload import page_channels_upload
from app.ui.pages.cohort_retention import page_cohort_retention
from app.ui.pages.collector import page_collector
from app.ui.pages.content_impact import page_content_impact
from app.ui.pages.customer_identity import page_customer_identity
from app.ui.pages.customers import page_customers
from app.ui.pages.data_browse import page_data
from app.ui.pages.data_dictionary import page_data_dictionary
from app.ui.pages.db_status import page_db_status
from app.ui.pages.kpi_overview import page_kpi_overview
from app.ui.pages.logs import page_logs
from app.ui.pages.media import page_media
from app.ui.pages.media_traffic import page_media_traffic
from app.ui.pages.pgy_dashboard import page_pgy_dashboard
from app.ui.pages.sql_console import page_sql_console
from app.ui.pages.upload import page_upload
from app.ui.pages.user_management import page_user_management
from app.ui.pages.weekly_report import page_weekly_report
from app.ui.pages.xhs_upload import page_xhs_upload
from app.ui.pages.zhihu_upload import page_zhihu_upload

Page = Callable[[], None]

ECOMMERCE = "ecommerce"
MEDIA = "media"
ADMIN = "admin"

SECTION_LABELS = {ECOMMERCE: "商城数据", MEDIA: "自媒体", ADMIN: "系统管理"}

ECOMMERCE_PAGES: dict[str, Page] = {
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

MEDIA_PAGES: dict[str, Page] = {
    "周报":           page_weekly_report,
    "公众号流量":     page_media_traffic,
    "公众号内容分析": page_media,
    "内容带货分析":   page_content_impact,
    "小红书数据":     page_xhs_upload,
    "蒲公英合作":     page_pgy_dashboard,
    "知乎数据":       page_zhihu_upload,
    "视频号数据":     page_channels_upload,
}

ADMIN_PAGES: dict[str, Page] = {
    "用户管理":   page_user_management,
    "操作日志":   page_logs,
    "数据库状态": page_db_status,
    "自动采集":   page_collector,
}

#: Viewers get the pages they need to hand data in and look it up, nothing else.
VIEWER_ECOMMERCE = frozenset({"数据上传", "数据浏览", "数据字典"})


def pages_for(role: str, is_admin: bool) -> tuple[dict[str, Page], dict[str, Page], dict[str, Page]]:
    """Return (ecommerce, media, admin) page maps visible to this role."""
    if role == "viewer":
        ecommerce = {k: v for k, v in ECOMMERCE_PAGES.items() if k in VIEWER_ECOMMERCE}
        media: dict[str, Page] = {}
    else:
        ecommerce = dict(ECOMMERCE_PAGES)
        media = dict(MEDIA_PAGES)
    admin = dict(ADMIN_PAGES) if is_admin else {}
    return ecommerce, media, admin


def all_pages(role: str, is_admin: bool) -> dict[str, Page]:
    ecommerce, media, admin = pages_for(role, is_admin)
    return {**ecommerce, **media, **admin}


def default_page(role: str) -> str:
    """Landing page, and the fallback when a URL names a page this role lacks."""
    return "数据浏览" if role == "viewer" else "KPI 看板"


def section_of(page: str) -> str | None:
    """Which section a page belongs to, ignoring role — used to sync the nav
    when someone arrives on a direct page link rather than by clicking."""
    if page in ECOMMERCE_PAGES:
        return ECOMMERCE
    if page in MEDIA_PAGES:
        return MEDIA
    if page in ADMIN_PAGES:
        return ADMIN
    return None

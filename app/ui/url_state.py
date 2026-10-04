"""Typed, non-sensitive filters that can safely travel in a page URL."""
from datetime import date


PAGE_FILTERS: dict[str, dict[str, object]] = {
    "数据分析": {
        "analysis_start": "date", "analysis_end": "date",
        "analysis_platform": {"全部", "youzan", "jd", "tmall"},
        "analysis_mode": {"概览", "新老客户"},
    },
    "数据浏览": {"orders_platform": {"全部", "有赞", "京东", "天猫"}},
    "客户管理": {
        "cust_start": "date", "cust_end": "date", "cust_min_orders": (1, 1000000),
        "cust_platform": {"全部", "youzan", "jd", "tmall"},
    },
    "跨平台客户": {"ident_start": "date", "ident_end": "date"},
    "客户留存": {
        "cohort_start": "date", "cohort_end": "date", "cohort_max_offset": (3, 24),
        "cohort_platform": {"全部", "youzan", "jd", "tmall"},
        "cohort_lens": {"累计回归曲线", "逐期留存三角"},
    },
    "公众号内容分析": {"media_start": "date", "media_end": "date"},
    "公众号流量": {"traffic_start": "date", "traffic_end": "date"},
    "内容带货分析": {
        "ci_start": "date", "ci_end": "date", "ci_window": (1, 30),
        "ci_platform": {"全部", "youzan", "jd", "tmall"},
        "ci_source": {"微信", "小红书", "知乎"},
    },
    "小红书数据": {"xhs_start": "date", "xhs_end": "date"},
    "知乎数据": {
        "zhihu_start_article": "date", "zhihu_end_article": "date",
        "zhihu_start_qa": "date", "zhihu_end_qa": "date",
    },
    "视频号数据": {"channels_start": "date", "channels_end": "date"},
}
ALL_FILTER_KEYS = frozenset(key for fields in PAGE_FILTERS.values() for key in fields)


def _parse_value(value: str, rule: object):
    if not isinstance(value, str) or len(value) > 50:
        return None
    if rule == "date":
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(rule, tuple):
        try:
            number = int(value)
        except ValueError:
            return None
        return number if rule[0] <= number <= rule[1] else None
    if isinstance(rule, set):
        return value if value in rule else None
    return None


def parse_shared_filters(page: str, params) -> dict:
    parsed = {}
    for key, rule in PAGE_FILTERS.get(page, {}).items():
        value = params.get(key)
        if value is None:
            continue
        valid = _parse_value(value, rule)
        if valid is not None:
            parsed[key] = valid
    return parsed


def shared_filter_params(page: str, state) -> dict[str, str]:
    values = {}
    for key, rule in PAGE_FILTERS.get(page, {}).items():
        value = state.get(key)
        if value is None:
            continue
        encoded = str(value)
        if _parse_value(encoded, rule) is not None:
            values[key] = encoded
    return values

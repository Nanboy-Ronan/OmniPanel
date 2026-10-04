from datetime import date

from app.ui.url_state import parse_shared_filters, shared_filter_params


def test_shared_filters_restore_valid_dates_and_choices():
    restored = parse_shared_filters("客户留存", {
        "cohort_start": "2026-01-01", "cohort_end": "2026-09-27",
        "cohort_platform": "jd", "cohort_max_offset": "12",
    })
    assert restored == {
        "cohort_start": date(2026, 1, 1), "cohort_end": date(2026, 9, 27),
        "cohort_platform": "jd", "cohort_max_offset": 12,
    }


def test_shared_urls_exclude_sensitive_search_and_invalid_values():
    state = {"orders_platform": "京东", "orders_search": "13800138000",
             "cust_start": date(2026, 1, 1), "cust_search": "上海市某路"}
    assert shared_filter_params("数据浏览", state) == {"orders_platform": "京东"}
    assert shared_filter_params("客户管理", state) == {"cust_start": "2026-01-01"}
    assert parse_shared_filters("客户留存", {"cohort_max_offset": "999", "cohort_start": "bad"}) == {}

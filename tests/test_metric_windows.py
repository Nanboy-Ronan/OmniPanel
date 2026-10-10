"""Metric-definition regressions: equal-length KPI comparison windows."""
from conftest import upload_and_poll
from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401


def test_kpi_month_comparison_uses_equal_day_counts(client, tokens):
    """03-31 month-to-date (31 days) used to be compared with February's 28
    days. The prior window is now the 31 days ending 02-28 (01-29..02-28)."""
    csv = "\n".join([
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额",
        "kp1,2026-01-28 10:00:00,13900000001,item,1,10",   # outside: 32 days back
        "kp2,2026-01-30 10:00:00,13900000002,item,1,10",   # inside the 31-day prior window
        "kp3,2026-02-15 10:00:00,13900000003,item,1,10",
        "kp4,2026-03-10 10:00:00,13900000004,item,1,10",
        "kp5,2026-03-31 10:00:00,13900000005,item,1,10",
    ])
    upload_and_poll(client, _auth(tokens["admin"]), csv.encode(), "kpi.csv")
    data = client.get("/analysis/kpi-periods", params={"anchor": "2026-03-31"},
                      headers=_auth(tokens["analyst"])).json()
    assert data["month"]["orders"] == 2
    assert data["prior_month"]["orders"] == 2

    # Mid-month anchors keep the ordinary same-day-of-month window.
    mid = client.get("/analysis/kpi-periods", params={"anchor": "2026-03-15"},
                     headers=_auth(tokens["analyst"])).json()
    assert mid["prior_month"]["orders"] == 1  # 02-01..02-15

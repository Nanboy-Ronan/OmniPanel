from datetime import date
from decimal import Decimal
import app.db as db
from app.db.models import Customer, Order
from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401


def seed_dashboard():
    with db.SyncSessionLocal() as session:
        session.add_all(
            [
                Customer(
                    customer_key=k, first_order_date=date(2026, 1, 1), platform="youzan"
                )
                for k in ["a", "b", "c"]
            ]
        )
        session.flush()
        values = [
            ("2026-01-01", "a", "youzan", "A", "上海", 100),
            ("2026-01-02", "b", "jd", "B", "北京", 50),
            ("2026-01-03", "a", "youzan", "A", "上海", 200),
            ("2026-01-03", "a", "youzan", "B", "上海", -20),
            ("2026-01-04", "b", "jd", "C", None, None),
            ("2026-01-04", "c", "jd", None, None, 0),
        ]
        for i, (day, customer, platform, sku, province, price) in enumerate(values):
            session.add(
                Order(
                    order_id=f"bi-{i}",
                    order_date=date.fromisoformat(day),
                    customer_key=customer,
                    platform=platform,
                    sku=sku,
                    province=province,
                    price=Decimal(price) if price is not None else None,
                )
            )
        session.commit()


def test_dashboard_complete_window_and_comparable_dates(client, tokens):
    seed_dashboard()
    r = client.get(
        "/analysis/dashboard",
        params={"start_date": "2026-01-03", "end_date": "2026-01-05"},
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["current"] == {
        "orders": 4,
        "priced_orders": 3,
        "revenue": 180.0,
        "customers": 3,
        "aov": 60.0,
        "missing_amount": 1,
    }
    assert data["prior"]["revenue"] == 150
    assert data["prior_start"] == "2025-12-31" and data["prior_end"] == "2026-01-02"
    assert [r["prior_date"] for r in data["series"]] == [
        "2025-12-31",
        "2026-01-01",
        "2026-01-02",
    ]
    assert data["series"][-1]["orders"] == 0 and data["series"][-1]["aov"] is None
    assert data["series"][1]["aov"] == 0  # genuine zero amount, not missing
    assert sum(r["revenue"] for r in data["series"]) == data["current"]["revenue"]
    assert (
        sum(r["current"]["revenue"] - r["prior"]["revenue"] for r in data["channels"])
        == 30
    )
    assert {r["name"] for r in data["regions"]} == {"上海", "未标注地区"}
    assert data["products"][0]["name"] == "A" and data["products"][-1]["revenue"] == -20


def test_dashboard_platform_filter_and_empty_window(client, tokens):
    seed_dashboard()
    data = client.get(
        "/analysis/dashboard",
        params={
            "start_date": "2026-01-03",
            "end_date": "2026-01-05",
            "platform": "youzan",
        },
        headers=_auth(tokens["analyst"]),
    ).json()
    assert data["current"]["orders"] == 2 and data["current"]["customers"] == 1
    assert data["prior"]["revenue"] == 100 and len(data["channels"]) == 1
    assert all(r["name"] != "未标注地区" for r in data["regions"])
    empty = client.get(
        "/analysis/dashboard",
        params={"start_date": "2026-03-01", "end_date": "2026-03-02"},
        headers=_auth(tokens["analyst"]),
    ).json()
    assert empty["current"]["orders"] == 0 and empty["channels"] == []
    assert len(empty["series"]) == 2


def test_dashboard_access_and_date_validation(client, tokens):
    params = {"start_date": "2026-01-03", "end_date": "2026-01-05"}
    assert (
        client.get(
            "/analysis/dashboard", params=params, headers=_auth(tokens["viewer"])
        ).status_code
        == 403
    )
    for start, end in [
        ("2026-01-05", "2026-01-03"),
        ("2020-01-01", "2026-01-01"),
        ("0001-01-01", "0001-01-02"),
    ]:
        assert (
            client.get(
                "/analysis/dashboard",
                params={"start_date": start, "end_date": end},
                headers=_auth(tokens["analyst"]),
            ).status_code
            == 422
        )

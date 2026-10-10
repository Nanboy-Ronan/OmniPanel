from datetime import date, datetime
import app.db as db
from app.db.models import XhsAccount, PgyNote, CollectorRun
from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401


def seed():
    with db.SyncSessionLocal() as s:
        a = XhsAccount(name="Account one", account_type="company")
        b = XhsAccount(name="Account two", account_type="company")
        s.add_all([a, b])
        s.flush()
        for note_id, day, quote, fee, interactions in [
            ("a", date(2026, 1, 20), 100, None, 10),
            ("b", date(2026, 8, 12), None, 20, 2),
            ("c", None, 80, 8, 8),
        ]:
            s.add(
                PgyNote(
                    account_id=a.id,
                    note_id=note_id,
                    publish_date=day,
                    blogger_nickname="Blogger",
                    cooperation_name="Campaign",
                    blogger_quote=quote,
                    service_fee=fee,
                    interactions=interactions,
                )
            )
        s.add_all(
            [
                CollectorRun(
                    platform="pugongying",
                    account_id=a.id,
                    started_at=datetime(2026, 10, 1),
                    status="success",
                ),
                CollectorRun(
                    platform="pugongying",
                    account_id=a.id,
                    started_at=datetime(2026, 10, 2),
                    status="session_expired",
                    error_message="private collector diagnostic",
                ),
                CollectorRun(
                    platform="pugongying",
                    account_id=b.id,
                    started_at=datetime(2026, 10, 3),
                    status="success",
                ),
            ]
        )
        s.commit()
        return a.id, b.id


def test_source_status_distinguishes_history_and_selected_account(client, tokens):
    account, other = seed()
    headers = _auth(tokens["analyst"])
    d = client.get(
        "/data/source-status", params={"source": "pgy"}, headers=headers
    ).json()
    assert d["records"] == 3 and d["undated"] == 1
    assert d["first_date"] == "2026-01-20" and d["last_date"] == "2026-08-12"
    assert sorted(r["status"] for r in d["runs"]) == ["session_expired", "success"]
    assert "private collector diagnostic" not in str(d)
    empty = client.get(
        "/data/source-status",
        params={"source": "pgy", "account_id": other},
        headers=headers,
    ).json()
    assert empty["records"] == 0 and len(empty["runs"]) == 1
    assert empty["first_date"] is None
    filtered = client.get(
        "/media/pgy/notes",
        params={"start_date": "2026-09-01", "end_date": "2026-10-01"},
        headers=headers,
    ).json()
    assert filtered == []
    assert len(client.get("/media/pgy/notes", headers=headers).json()) == 3


def test_pgy_aggregates_include_known_fees_when_one_cost_component_is_missing(
    client, tokens
):
    seed()
    headers = _auth(tokens["analyst"])
    for endpoint in ["bloggers", "campaigns"]:
        d = client.get("/media/pgy/" + endpoint, headers=headers).json()
        assert d[0]["total_spend"] == 208


def test_source_status_authorization_and_source_validation(client, tokens):
    assert (
        client.get(
            "/data/source-status?source=pgy", headers=_auth(tokens["viewer"])
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/data/source-status?source=other", headers=_auth(tokens["analyst"])
        ).status_code
        == 422
    )
    for source in ["orders", "wechat", "traffic", "xhs", "zhihu", "channels", "pgy"]:
        response = client.get(
            "/data/source-status",
            params={"source": source},
            headers=_auth(tokens["analyst"]),
        )
        assert response.status_code == 200, response.text
        assert response.json()["records"] == 0


def test_source_status_runs_ignore_verify_runs(client, tokens):
    """An evening verify-all run must not replace the latest collect run."""
    with db.SyncSessionLocal() as s:
        acc = XhsAccount(name="Verify acc", account_type="company")
        s.add(acc)
        s.flush()
        s.add_all([
            CollectorRun(platform="xhs", account_id=acc.id, started_at=datetime(2026, 10, 1, 6, 30),
                         finished_at=datetime(2026, 10, 1, 6, 40), status="download_failed"),
            CollectorRun(platform="xhs", account_id=acc.id, started_at=datetime(2026, 10, 1, 21, 0),
                         finished_at=datetime(2026, 10, 1, 21, 1), status="success", triggered_by="verify"),
        ])
        s.commit()
        acc_id = acc.id
    d = client.get("/data/source-status", params={"source": "xhs", "account_id": acc_id},
                   headers=_auth(tokens["analyst"])).json()
    assert [r["status"] for r in d["runs"]] == ["download_failed"]

    fresh = client.get("/data/freshness", headers=_auth(tokens["analyst"])).json()
    assert fresh["xhs"]["last_collect_at"] is None  # verify success is not a collect


def test_freshness_reports_per_platform_order_staleness(client, tokens):
    from app.db.models import Customer, Order, UploadBatch

    with db.SyncSessionLocal() as s:
        s.add(Customer(customer_key="k1", platform="youzan", first_order_date=date(2026, 9, 1)))
        s.flush()
        s.add_all([
            Order(order_id="y1", order_date=date(2026, 10, 7), customer_key="k1", platform="youzan", price=10),
            Order(order_id="t1", order_date=date(2026, 9, 20), customer_key="k1", platform="tmall", price=10),
            Order(order_id="j1", order_date=date(2026, 10, 5), customer_key="k1", platform="jd", price=10),
            UploadBatch(filename="t.xlsx", platform="tmall", file_sha256="x", status="completed"),
        ])
        s.add(CollectorRun(platform="zhihu", content_type="article", status="success",
                           finished_at=datetime(2026, 10, 8, 6, 40)))
        s.commit()

    data = client.get("/data/freshness", headers=_auth(tokens["analyst"])).json()
    orders = data["orders"]
    # Backward compatible: the cross-platform max is still reported.
    assert orders["coverage_through"] == "2026-10-07"
    assert orders["platforms"]["youzan"]["stale"] is False
    assert orders["platforms"]["jd"]["stale"] is False  # 2 days behind: within tolerance
    assert orders["platforms"]["tmall"] == {
        "coverage_through": "2026-09-20",
        "last_import_at": orders["platforms"]["tmall"]["last_import_at"],
        "stale": True,
    }
    assert orders["platforms"]["tmall"]["last_import_at"] is not None
    assert orders["stale"] is True and orders["stale_platforms"] == ["tmall"]
    assert data["zhihu"]["last_collect_at"].startswith("2026-10-08T06:40")
    assert set(data) == {"orders", "wechat", "xhs", "zhihu", "channels", "pgy"}

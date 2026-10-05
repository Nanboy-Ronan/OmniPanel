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

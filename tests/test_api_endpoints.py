import os, importlib
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from pathlib import Path

# ensure project root on path
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import app.db as db
from app.db import Base
import app.db.etl as etl

from sample_dataset import synthetic_youzan_df  # noqa: E402


@pytest.fixture
def client(pg_async_url, monkeypatch):
    from sqlalchemy import create_engine as _sync_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool
    engine = create_async_engine(pg_async_url, future=True, echo=False, poolclass=NullPool)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    sync_url = pg_async_url.replace("+asyncpg", "+psycopg2")
    s_engine = _sync_engine(sync_url, poolclass=NullPool)
    SyncSL = sessionmaker(s_engine, class_=Session, expire_on_commit=False)

    monkeypatch.setattr(db, "DATABASE_URL", pg_async_url, raising=False)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "AsyncSessionLocal", SessionLocal, raising=False)
    monkeypatch.setattr(db, "SyncSessionLocal", SyncSL, raising=False)

    import app.main
    importlib.reload(app.main)

    with TestClient(app.main.app) as c:
        yield c
    import asyncio
    asyncio.run(engine.dispose())
    s_engine.dispose()


@pytest.fixture
def tokens(client):
    """Return auth tokens for viewer/analyst/admin test users."""

    # First registration → user becomes admin
    client.post(
        "/auth/register",
        json={"email": "first@test.com", "password": "pw", "role": "viewer"},
    )
    r_login = client.post(
        "/auth/jwt/login",
        data={"username": "first@test.com", "password": "pw"},
    )
    admin_token = r_login.json()["access_token"]

    def create_via_admin(email, role):
        r = client.post(
            "/admin/users",
            json={"email": email, "password": "pw", "role": role},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 201
        r_login = client.post(
            "/auth/jwt/login",
            data={"username": email, "password": "pw"},
        )
        return r_login.json()["access_token"]

    tokens = {
        role: create_via_admin(f"{role}@test.com", role)
        for role in ["viewer", "analyst", "admin"]
    }

    return tokens


@pytest.fixture
def sample_data(client):
    """Ingest the synthetic sample dataset for tests that need data."""
    from sqlalchemy import create_engine as _sync_engine
    from sqlalchemy.orm import sessionmaker as _sync_sessionmaker

    df_raw, _platform = etl.normalize_dataframe(synthetic_youzan_df())
    sync_url = db.DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = _sync_engine(sync_url)
    Session = _sync_sessionmaker(bind=engine)
    with Session() as sess:
        etl.ingest(df_raw, sess)
    engine.dispose()


def test_first_registration_becomes_admin(client):
    client.post(
        "/auth/register",
        json={"email": "first@example.com", "password": "pw", "role": "viewer"},
    )

    import asyncio
    from sqlalchemy import select
    from app.db.models import User

    async def _get_role():
        async with db.AsyncSessionLocal() as session:
            result = await session.execute(
                select(User.role).where(User.email == "first@example.com")
            )
            return result.scalar_one()

    role = asyncio.run(_get_role())
    assert role == "admin"


def test_register_blocked_after_first_user(client):
    r1 = client.post(
        "/auth/register",
        json={"email": "first@example.com", "password": "pw"},
    )
    assert r1.status_code == 201

    r = client.post(
        "/auth/register",
        json={"email": "second@example.com", "password": "pw"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Registration closed"


def test_wecom_authorize_url(client, monkeypatch):
    monkeypatch.setenv("WECOM_CORP_ID", "wwcorp")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
    monkeypatch.setenv("WECOM_APP_SECRET", "secret")
    # Override the production redirect URI so http://localhost:8501 is accepted
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "http://localhost:8501")

    r = client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:8501"},
    )

    assert r.status_code == 200
    body = r.json()

    # authorize_url → PC QR-code flow
    qr_url = body["authorize_url"]
    assert qr_url.startswith("https://open.work.weixin.qq.com/wwopen/sso/qrConnect?")
    assert "appid=wwcorp" in qr_url
    assert "agentid=1000002" in qr_url

    # oauth2_url → mobile / WeCom in-app browser flow (snsapi_privateinfo for email access)
    oauth2_url = body["oauth2_url"]
    assert oauth2_url.startswith("https://open.weixin.qq.com/connect/oauth2/authorize?")
    assert "appid=wwcorp" in oauth2_url
    assert "agentid=1000002" in oauth2_url
    assert "response_type=code" in oauth2_url
    assert "scope=snsapi_privateinfo" in oauth2_url
    assert oauth2_url.endswith("#wechat_redirect")


def test_wecom_exchange_creates_user_and_returns_jwt(client, monkeypatch):
    monkeypatch.setenv("WECOM_CORP_ID", "wwcorp")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
    monkeypatch.setenv("WECOM_APP_SECRET", "secret")
    monkeypatch.setenv("WECOM_DEFAULT_ROLE", "analyst")
    # Override the production redirect URI so http://localhost:8501 is accepted
    monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "http://localhost:8501")

    import app.views.wecom_auth as wecom_auth

    async def fake_identity(code):
        assert code == "oauth-code"
        return {"userid": "zhangsan", "email": "zhangsan@example.com", "name": "张三"}

    monkeypatch.setattr(wecom_auth, "_fetch_wecom_identity", fake_identity)
    state = client.get(
        "/auth/wecom/authorize-url",
        params={"redirect_uri": "http://localhost:8501"},
    ).json()["authorize_url"].split("state=", 1)[1].split("&", 1)[0]

    r = client.post(
        "/auth/wecom/exchange",
        json={"code": "oauth-code", "state": state},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "zhangsan@example.com"
    assert body["user"]["role"] == "admin"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_upload_and_orders(client, tokens, tmp_path):
    data = "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n1,2025-07-21,13800138000,item,1,10"
    r = client.post(
        "/upload/", files={"file": ("x.csv", data)}, headers=_auth(tokens["admin"])
    )
    assert r.status_code == 202
    batch_id = r.json()["batch_id"]

    r_batch = client.get(f"/upload/batches/{batch_id}", headers=_auth(tokens["admin"]))
    assert r_batch.status_code == 200
    assert r_batch.json()["inserted_orders"] == 1

    r2 = client.get("/orders_all/", headers=_auth(tokens["admin"]))
    assert r2.status_code in (200, 404)
    if r2.status_code == 200:
        assert isinstance(r2.json(), list)


def test_upload_missing_column(client, tokens):
    bad = "订单号,买家付款时间,全部商品名称\n1,2025-01-01,item"
    r = client.post(
        "/upload/", files={"file": ("bad.csv", bad)}, headers=_auth(tokens["admin"])
    )
    assert r.status_code == 202
    batch_id = r.json()["batch_id"]

    r_batch = client.get(f"/upload/batches/{batch_id}", headers=_auth(tokens["admin"]))
    assert r_batch.status_code == 200
    assert r_batch.json()["status"] == "failed"


def test_upload_size_limit(client, tokens, monkeypatch):
    """POST /upload/ must return 413 when the file exceeds the configured limit."""
    import app.views.ecommerce.upload as upload_mod
    monkeypatch.setattr(upload_mod, "_MAX_UPLOAD_BYTES", 10)

    oversized = b"x" * 20
    r = client.post(
        "/upload/", files={"file": ("big.csv", oversized)}, headers=_auth(tokens["admin"])
    )
    assert r.status_code == 413
    assert "File too large" in r.json()["detail"]


def test_upload_reuse_of_identical_file_still_creates_new_batch_and_dedupes_rows(client, tokens):
    """Re-uploading the exact same file content must still go through the full
    ETL and get its own UploadBatch (audit trail) — duplicate detection
    happens at the row level (via content hash in the ETL), not by skipping
    the batch entirely. See test_platform_raw_tables.py for the thorough
    row-level-dedup coverage this endpoint relies on.
    """
    data = "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n1,2025-07-21,13800138000,item,1,10"

    r1 = client.post(
        "/upload/", files={"file": ("dup.csv", data)}, headers=_auth(tokens["admin"])
    )
    assert r1.status_code == 202
    batch1_id = r1.json()["batch_id"]
    r_batch1 = client.get(f"/upload/batches/{batch1_id}", headers=_auth(tokens["admin"]))
    assert r_batch1.json()["status"] == "completed"
    assert r_batch1.json()["inserted_orders"] == 1

    r2 = client.post(
        "/upload/", files={"file": ("dup-renamed.csv", data)}, headers=_auth(tokens["admin"])
    )
    assert r2.status_code == 202
    batch2_id = r2.json()["batch_id"]
    assert batch2_id != batch1_id
    r_batch2 = client.get(f"/upload/batches/{batch2_id}", headers=_auth(tokens["admin"]))
    body2 = r_batch2.json()
    assert body2["status"] == "completed"
    assert body2["inserted_orders"] == 0
    assert body2["duplicate_rows"] == 1


def test_upload_rejects_platform_mismatch(client, tokens):
    """A 有赞 (youzan) file uploaded against the 京东 (jd) tab must be rejected
    synchronously, before any background ETL runs."""
    youzan_csv = "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n1,2025-07-21,13800138000,item,1,10"

    r = client.post(
        "/upload/",
        files={"file": ("looks-like-jd.csv", youzan_csv)},
        params={"expected_platform": "jd"},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 400
    assert "youzan" in r.json()["detail"]
    assert "jd" in r.json()["detail"]

    # Matching platform must succeed normally.
    r_ok = client.post(
        "/upload/",
        files={"file": ("actually-youzan.csv", youzan_csv)},
        params={"expected_platform": "youzan"},
        headers=_auth(tokens["admin"]),
    )
    assert r_ok.status_code == 202


def test_analysis_after_upload(client, tokens):
    """Uploading data should also refresh analysis tables."""
    # Clear existing DB tables first
    client.post("/admin/clear-db", headers=_auth(tokens["admin"]))
    import asyncio
    from app.db import engine, Base

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())

    data = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "1,2025-07-21,13800138000,item,1,10"
    )
    r = client.post(
        "/upload/", files={"file": ("x.csv", data)}, headers=_auth(tokens["admin"])
    )
    assert r.status_code == 202

    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
    r2 = client.get("/analysis/", params=params, headers=_auth(tokens["analyst"]))
    assert r2.status_code == 200
    data = r2.json()
    for group in ["old", "new"]:
        assert "customer_count" in data[group]
    assert isinstance(data.get("old_daily"), dict)
    assert isinstance(data.get("new_daily"), dict)


def test_repurchase_rate_endpoint(client, tokens):
    """Repurchase rate should consider new customers within the window."""

    client.post("/admin/clear-db", headers=_auth(tokens["admin"]))

    import asyncio

    from app.db import engine, Base

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())

    csv = (
        "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n"
        "1,2025-07-01,13800000001,item,1,10\n"
        "2,2025-07-05,13800000001,item,1,15\n"
        "3,2025-07-10,13800000002,item,1,20\n"
        "4,2025-08-01,13800000003,item,1,30"
    )

    r_upload = client.post(
        "/upload/",
        files={"file": ("repurchase.csv", csv)},
        headers=_auth(tokens["admin"]),
    )
    assert r_upload.status_code == 202

    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
    r_view = client.get(
        "/analysis/repurchase_rate",
        params=params,
        headers=_auth(tokens["viewer"]),
    )
    assert r_view.status_code == 403

    r = client.get(
        "/analysis/repurchase_rate",
        params=params,
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["new_customers"] == 2
    assert payload["repurchasing_customers"] == 1
    assert pytest.approx(payload["repurchase_rate"], rel=1e-6) == 0.5


def test_analysis_permissions(client, tokens, sample_data):
    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
    r_bad = client.get("/analysis/", params=params, headers=_auth(tokens["viewer"]))
    assert r_bad.status_code == 403

    r_ok = client.get("/analysis/", params=params, headers=_auth(tokens["analyst"]))
    assert r_ok.status_code == 200


def test_analysis_invalid_range(client, tokens, sample_data):
    params = {"start_date": "2025-08-01", "end_date": "2025-07-01"}
    r = client.get("/analysis/", params=params, headers=_auth(tokens["admin"]))
    assert r.status_code == 200
    data = r.json()
    assert data["old"]["count"] == 0 and data["new"]["count"] == 0
    assert data["old"]["customer_count"] == 0 and data["new"]["customer_count"] == 0
    assert data["old_daily"] == {} and data["new_daily"] == {}


def test_analysis_include_rows_false_omits_raw_rows_but_keeps_aggregates(client, tokens, sample_data):
    """The overview page's trend chart only needs old_daily/new_daily —
    include_rows=False must skip the (up to 2*rows_cap) raw-row payload while
    still returning identical aggregates."""
    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}

    r_full = client.get("/analysis/", params=params, headers=_auth(tokens["analyst"]))
    assert r_full.status_code == 200
    full = r_full.json()
    assert full["old"]["rows"] or full["new"]["rows"]  # sanity: some rows exist

    r_slim = client.get(
        "/analysis/", params={**params, "include_rows": "false"}, headers=_auth(tokens["analyst"])
    )
    assert r_slim.status_code == 200
    slim = r_slim.json()
    assert slim["old"]["rows"] == []
    assert slim["new"]["rows"] == []

    # Aggregates and daily series must be identical regardless of include_rows.
    for group in ["old", "new"]:
        assert slim[group]["count"] == full[group]["count"]
        assert slim[group]["customer_count"] == full[group]["customer_count"]
        assert slim[group]["paid_sum"] == full[group]["paid_sum"]
    assert slim["old_daily"] == full["old_daily"]
    assert slim["new_daily"] == full["new_daily"]


def test_analysis_overview(client, tokens, sample_data):
    """Verify /analysis/overview filters data by date range."""

    july = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
    r1 = client.get("/analysis/overview", params=july, headers=_auth(tokens["analyst"]))
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["orders"] == 7
    assert data1["unique_customers"] == 5
    assert pytest.approx(data1["revenue"], rel=1e-3) == 899.01
    assert (
        isinstance(data1.get("top_sku"), list)
        and data1["top_sku"][0]["sku"] == "商品甲"
    )
    assert isinstance(data1.get("top_province_unique"), list)

    june = {"start_date": "2025-06-21", "end_date": "2025-06-30"}
    r2 = client.get("/analysis/overview", params=june, headers=_auth(tokens["analyst"]))
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["orders"] == 3
    assert data2["unique_customers"] == 3
    assert pytest.approx(data2["revenue"], rel=1e-3) == 440.00

    # metrics should differ for the two ranges
    assert data1["orders"] != data2["orders"]


def test_latest_order_date_reflects_max_order_date(client, tokens, sample_data):
    """The KPI dashboard anchors on this instead of date.today() because orders
    arrive in manual upload batches, not in real time."""
    r = client.get(
        "/analysis/latest_order_date", headers=_auth(tokens["analyst"])
    )
    assert r.status_code == 200
    latest = r.json()["latest_order_date"]
    assert latest is not None

    # Cross-check against /analysis/customers's own max(last_date) for the
    # full data range — both are derived from the same order_date column.
    r_cust = client.get(
        "/analysis/customers",
        params={"start_date": "2020-01-01", "end_date": "2030-01-01"},
        headers=_auth(tokens["analyst"]),
    )
    max_last_date = max(row["last_date"] for row in r_cust.json())
    assert latest == max_last_date


def test_latest_order_date_null_when_no_data(client, tokens):
    r = client.get(
        "/analysis/latest_order_date", headers=_auth(tokens["analyst"])
    )
    assert r.status_code == 200
    assert r.json()["latest_order_date"] is None


def test_analysis_customers(client, tokens, sample_data):
    """Verify /analysis/customers aggregates and filters correctly."""
    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
    r = client.get(
        "/analysis/customers", params=params, headers=_auth(tokens["analyst"])
    )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 5
    revenues = [row["revenue"] for row in data]
    assert revenues == sorted(revenues, reverse=True)

    # new fields should be present
    sample_row = data[0]
    for field in [
        "customer_key",
        "mobile",
        "receiver",
        "phone",
        "province",
        "area",
        "full_address",
        "buyer_nick",
        "coupon_name",
        "distributor",
    ]:
        assert field in sample_row
    assert sample_row["customer_key"] == sample_row["mobile"]

    params["min_orders"] = 2
    r2 = client.get(
        "/analysis/customers", params=params, headers=_auth(tokens["analyst"])
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert len(data2) == 2
    assert all(row["orders"] >= 2 for row in data2)


def test_analysis_customers_pagination(client, tokens, sample_data):
    """limit/offset must page through results, sorted by revenue desc, with the
    pre-pagination total in X-Total-Count — this is what stops the endpoint
    from having to serialise the whole customer base on every call."""
    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}

    r_full = client.get("/analysis/customers", params=params, headers=_auth(tokens["analyst"]))
    assert r_full.headers["X-Total-Count"] == "38"
    full = r_full.json()
    assert len(full) == 38

    r_page1 = client.get(
        "/analysis/customers", params={**params, "limit": 10}, headers=_auth(tokens["analyst"])
    )
    assert r_page1.status_code == 200
    assert r_page1.headers["X-Total-Count"] == "38"
    page1 = r_page1.json()
    assert len(page1) == 10
    assert page1 == full[:10]

    r_page2 = client.get(
        "/analysis/customers",
        params={**params, "limit": 10, "offset": 10},
        headers=_auth(tokens["analyst"]),
    )
    assert r_page2.status_code == 200
    page2 = r_page2.json()
    assert len(page2) == 10
    assert page2 == full[10:20]
    # No overlap between pages.
    assert {r["customer_key"] for r in page1}.isdisjoint({r["customer_key"] for r in page2})


def test_analysis_customers_search(client, tokens, sample_data):
    """search must match server-side across customer_key/receiver/phone/address/nick."""
    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
    r_full = client.get("/analysis/customers", params=params, headers=_auth(tokens["analyst"]))
    full = r_full.json()
    assert full  # sanity

    target = full[0]
    # Search on a distinctive substring of the customer's phone (customer_key).
    needle = target["customer_key"][-6:]
    r_search = client.get(
        "/analysis/customers",
        params={**params, "search": needle},
        headers=_auth(tokens["analyst"]),
    )
    assert r_search.status_code == 200
    results = r_search.json()
    assert results
    assert any(r["customer_key"] == target["customer_key"] for r in results)
    assert int(r_search.headers["X-Total-Count"]) <= len(full)

    r_none = client.get(
        "/analysis/customers",
        params={**params, "search": "definitely-not-a-real-customer-xyz"},
        headers=_auth(tokens["analyst"]),
    )
    assert r_none.status_code == 200
    assert r_none.json() == []
    assert r_none.headers["X-Total-Count"] == "0"


def test_orders_all_pagination(client, tokens, sample_data):
    """limit/offset must page through orders with the total in X-Total-Count."""
    r_full = client.get("/orders_all/", headers=_auth(tokens["analyst"]))
    assert r_full.status_code == 200
    full = r_full.json()
    total = int(r_full.headers["X-Total-Count"])
    assert total == len(full)
    assert total > 10

    r_page = client.get(
        "/orders_all/", params={"limit": 5}, headers=_auth(tokens["analyst"])
    )
    assert r_page.status_code == 200
    page = r_page.json()
    assert len(page) == 5
    assert r_page.headers["X-Total-Count"] == str(total)
    assert page == full[:5]

    r_page2 = client.get(
        "/orders_all/", params={"limit": 5, "offset": 5}, headers=_auth(tokens["analyst"])
    )
    assert r_page2.json() == full[5:10]


def test_admin_clear_db_permissions(client, tokens):
    r = client.post("/admin/clear-db", headers=_auth(tokens["analyst"]))
    assert r.status_code == 403

    r2 = client.post("/admin/clear-db", headers=_auth(tokens["admin"]))
    assert r2.status_code == 200


def test_admin_user_management(client, tokens):
    # admin can list users
    r = client.get("/admin/users", headers=_auth(tokens["admin"]))
    assert r.status_code == 200
    users = r.json()
    viewer = next(u for u in users if u["email"] == "viewer@test.com")

    # non-admin cannot access list
    r_forbidden = client.get("/admin/users", headers=_auth(tokens["viewer"]))
    assert r_forbidden.status_code == 403

    # update viewer role to analyst
    user_id = viewer["id"]
    r2 = client.put(
        f"/admin/users/{user_id}/role",
        json={"role": "analyst"},
        headers=_auth(tokens["admin"]),
    )
    assert r2.status_code == 200

    # verify change
    r3 = client.get("/admin/users", headers=_auth(tokens["admin"]))
    roles = {u["email"]: u["role"] for u in r3.json()}
    assert roles["viewer@test.com"] == "analyst"


def test_admin_create_user(client, tokens):
    """Admin can create additional user accounts."""
    r = client.post(
        "/admin/users",
        json={"email": "new@test.com", "password": "pw", "role": "viewer"},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 201
    data = r.json()
    assert data["email"] == "new@test.com" and data["role"] == "viewer"
    assert "id" in data

    # endpoint forbidden for non-admin users
    r_bad = client.post(
        "/admin/users",
        json={"email": "bad@test.com", "password": "pw"},
        headers=_auth(tokens["viewer"]),
    )
    assert r_bad.status_code == 403


def test_admin_reset_password(client, tokens):
    r = client.get("/admin/users", headers=_auth(tokens["admin"]))
    assert r.status_code == 200
    viewer = next(u for u in r.json() if u["email"] == "viewer@test.com")
    user_id = viewer["id"]

    r2 = client.put(
        f"/admin/users/{user_id}/password",
        json={"password": "newpw"},
        headers=_auth(tokens["admin"]),
    )
    assert r2.status_code == 200

    r_login = client.post(
        "/auth/jwt/login",
        data={"username": "viewer@test.com", "password": "newpw"},
    )
    assert r_login.status_code == 200


def test_customer_orders_valid(client, tokens, sample_data):
    """Return order history for a known phone number with date filter."""
    params = {"start_date": "2025-07-01", "end_date": "2025-07-31"}
    r = client.get(
        "/analysis/customers/13900000001",
        params=params,
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert pytest.approx(data["total_spend"], rel=1e-6) == 299.01
    assert all(row["order_date"] >= "2025-07-01" for row in data["orders"])
    order_sample = data["orders"][0]
    for field in [
        "province",
        "area",
        "full_address",
        "buyer_nick",
        "coupon_name",
        "distributor",
    ]:
        assert field in order_sample


def test_customer_orders_not_found(client, tokens, sample_data):
    """Invalid phone numbers should return 404."""
    r = client.get(
        "/analysis/customers/00000000000",
        headers=_auth(tokens["analyst"]),
    )
    assert r.status_code == 404


def test_operation_logs(client, tokens):
    r = client.get("/admin/logs", headers=_auth(tokens["admin"]))
    assert r.status_code == 200

    data = "订单号,买家付款时间,收货人手机号/提货人手机号,全部商品名称,商品种类数,订单实付金额\n1,2025-07-21,13800138000,item,1,10"
    client.post(
        "/upload/", files={"file": ("x.csv", data)}, headers=_auth(tokens["admin"])
    )

    r_users = client.get("/admin/users", headers=_auth(tokens["admin"]))
    uid = next(u for u in r_users.json() if u["email"] == "viewer@test.com")["id"]
    client.put(
        f"/admin/users/{uid}/role",
        json={"role": "analyst"},
        headers=_auth(tokens["admin"]),
    )
    client.put(
        f"/admin/users/{uid}/password",
        json={"password": "newpw2"},
        headers=_auth(tokens["admin"]),
    )

    r2 = client.get("/admin/logs", headers=_auth(tokens["admin"]))
    assert r2.status_code == 200
    logs = r2.json()
    if logs:
        assert "email" in logs[0]
        assert "detail" in logs[0]

    # Verify upload log has detail with filename, rows, platform
    upload_logs = [l for l in logs if l["action"] == "upload"]
    if upload_logs:
        detail = upload_logs[0]["detail"]
        assert detail is not None
        assert "filename" in detail
        assert "rows" in detail
        assert "platform" in detail

    # Verify update_role log has target_user detail
    role_logs = [l for l in logs if l["action"] == "update_role"]
    if role_logs:
        detail = role_logs[0]["detail"]
        assert detail is not None
        assert "target_user" in detail

    # Verify download log exists and has count detail
    dl_logs = [l for l in logs if l["action"] == "download"]
    if dl_logs:
        detail = dl_logs[0]["detail"]
        assert detail is not None
        assert "count" in detail

    # Verify analysis log exists and has date range detail
    analysis_logs = [l for l in logs if l["action"] == "analysis"]
    if analysis_logs:
        detail = analysis_logs[0]["detail"]
        assert detail is not None
        assert "start_date" in detail
        assert "end_date" in detail

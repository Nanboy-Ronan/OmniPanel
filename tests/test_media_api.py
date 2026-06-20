from datetime import date

from tests.test_api_endpoints import _auth, client, tokens  # noqa: F401


def _clear_numbered_wechat_env(monkeypatch):
    for idx in range(1, 11):
        monkeypatch.delenv(f"WECHAT_APP_ID_{idx}", raising=False)
        monkeypatch.delenv(f"WECHAT_APP_SECRET_{idx}", raising=False)
        monkeypatch.delenv(f"WECHAT_ACCOUNT_NAME_{idx}", raising=False)


def test_media_accounts_bootstrap_from_environment(client, tokens, monkeypatch):
    _clear_numbered_wechat_env(monkeypatch)
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_ID", "wx-env")
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_SECRET", "secret-env")
    monkeypatch.setenv("WECHAT_OFFICIAL_ACCOUNT_NAME", "Env Account")

    r = client.get("/media/accounts", headers=_auth(tokens["analyst"]))

    assert r.status_code == 200
    accounts = r.json()
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "wechat_official"
    assert accounts[0]["name"] == "Env Account"
    assert accounts[0]["app_id"] == "wx-env"


def test_media_accounts_bootstrap_numbered_environment(client, tokens, monkeypatch):
    monkeypatch.delenv("WECHAT_OFFICIAL_APP_ID", raising=False)
    monkeypatch.delenv("WECHAT_OFFICIAL_APP_SECRET", raising=False)
    monkeypatch.delenv("WECHAT_OFFICIAL_ACCOUNT_NAME", raising=False)
    monkeypatch.setenv("WECHAT_APP_ID_1", "wx-service-a")
    monkeypatch.setenv("WECHAT_APP_SECRET_1", "secret-a")
    monkeypatch.setenv("WECHAT_ACCOUNT_NAME_1", "Service A")
    monkeypatch.setenv("WECHAT_APP_ID_3", "wx-subscription-c")
    monkeypatch.setenv("WECHAT_APP_SECRET_3", "secret-c")
    monkeypatch.setenv("WECHAT_ACCOUNT_NAME_3", "Subscription C")

    r = client.get("/media/accounts", headers=_auth(tokens["analyst"]))

    assert r.status_code == 200
    accounts = sorted(r.json(), key=lambda item: item["app_id"])
    assert [(a["name"], a["app_id"]) for a in accounts] == [
        ("Service A", "wx-service-a"),
        ("Subscription C", "wx-subscription-c"),
    ]


def test_media_sync_requires_admin(client, tokens, monkeypatch):
    _clear_numbered_wechat_env(monkeypatch)
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_ID", "wx-env")
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_SECRET", "secret-env")

    r = client.post(
        "/media/wechat/sync",
        json={"start_date": "2026-05-20", "end_date": "2026-05-20"},
        headers=_auth(tokens["analyst"]),
    )

    assert r.status_code == 403


def test_media_sync_upserts_posts_and_metrics(client, tokens, monkeypatch):
    _clear_numbered_wechat_env(monkeypatch)
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_ID", "wx-env")
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_SECRET", "secret-env")
    monkeypatch.setenv("WECHAT_OFFICIAL_ACCOUNT_NAME", "Env Account")

    import app.views.media.routes as media_view

    class FakeClient:
        def __init__(self, app_id, app_secret):
            assert app_id == "wx-env"
            assert app_secret == "secret-env"

        def fetch_article_total_rows(self, start_date, end_date):
            assert start_date == date(2026, 5, 20)
            assert end_date == date(2026, 5, 20)
            return [
                {
                    "external_id": "msg-1_1",
                    "title": "First article",
                    "publish_date": date(2026, 5, 20),
                    "metric_date": date(2026, 5, 20),
                    "url": "http://mp.weixin.qq.com/s?__biz=xxx",
                    "read_user_count": 80,
                    "share_user_count": 4,
                    "like_user": 3,
                    "comment_count": 1,
                    "collection_user": 2,
                    "read_avg_time": 0.12,
                    "read_user_source": [{"user_count": 80, "scene_desc": "全部"}],
                    "raw_payload": {"mock": 1},
                }
            ]

    monkeypatch.setattr(media_view, "WeChatOfficialClient", FakeClient)

    payload = {"start_date": "2026-05-20", "end_date": "2026-05-20"}
    r1 = client.post("/media/wechat/sync", json=payload, headers=_auth(tokens["admin"]))
    r2 = client.post("/media/wechat/sync", json=payload, headers=_auth(tokens["admin"]))

    assert r1.status_code == 200
    assert r1.json()["posts_upserted"] == 1
    assert r1.json()["metrics_upserted"] == 1
    assert r2.status_code == 200

    posts = client.get(
        "/media/posts",
        params={"start_date": "2026-05-20", "end_date": "2026-05-20"},
        headers=_auth(tokens["analyst"]),
    )
    assert posts.status_code == 200
    rows = posts.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "First article"
    assert rows[0]["read_user_count"] == 80
    assert rows[0]["like_user"] == 3
    assert rows[0]["comment_count"] == 1
    assert rows[0]["collection_user"] == 2

    overview = client.get(
        "/media/overview",
        params={"start_date": "2026-05-20", "end_date": "2026-05-20"},
        headers=_auth(tokens["analyst"]),
    )
    assert overview.status_code == 200
    data = overview.json()
    assert data["posts"] == 1
    assert data["read_user_count"] == 80
    assert data["like_user"] == 3

    metrics = client.get(
        f"/media/posts/{rows[0]['id']}/metrics",
        headers=_auth(tokens["analyst"]),
    )
    assert metrics.status_code == 200
    m = metrics.json()[0]
    assert m["metric_date"] == "2026-05-20"
    assert m["read_user_count"] == 80
    assert m["like_user"] == 3
    assert m["read_avg_time"] == 0.12
    assert m["read_user_source"] == [{"user_count": 80, "scene_desc": "全部"}]


def test_media_metrics_use_latest_snapshot_not_sum(client, tokens, monkeypatch):
    """WeChat reports read_user_count cumulatively per day, so multi-day
    snapshots for the same article must not be summed (that double counts
    repeat readers). /posts and /overview should report the latest day's
    cumulative value, not the sum across days."""
    _clear_numbered_wechat_env(monkeypatch)
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_ID", "wx-env")
    monkeypatch.setenv("WECHAT_OFFICIAL_APP_SECRET", "secret-env")
    monkeypatch.setenv("WECHAT_OFFICIAL_ACCOUNT_NAME", "Env Account")

    import app.views.media.routes as media_view

    class FakeClient:
        def __init__(self, app_id, app_secret):
            pass

        def fetch_article_total_rows(self, start_date, end_date):
            return [
                {
                    "external_id": "msg-cumulative",
                    "title": "Cumulative article",
                    "publish_date": date(2026, 5, 20),
                    "metric_date": date(2026, 5, 20),
                    "url": "http://mp.weixin.qq.com/s?__biz=xxx",
                    "read_user_count": 80,
                    "share_user_count": 4,
                    "like_user": 3,
                    "comment_count": 1,
                    "collection_user": 2,
                    "raw_payload": {"day": 1},
                },
                {
                    "external_id": "msg-cumulative",
                    "title": "Cumulative article",
                    "publish_date": date(2026, 5, 20),
                    "metric_date": date(2026, 5, 21),
                    "url": "http://mp.weixin.qq.com/s?__biz=xxx",
                    "read_user_count": 150,
                    "share_user_count": 6,
                    "like_user": 5,
                    "comment_count": 2,
                    "collection_user": 3,
                    "raw_payload": {"day": 2},
                },
            ]

    monkeypatch.setattr(media_view, "WeChatOfficialClient", FakeClient)

    payload = {"start_date": "2026-05-20", "end_date": "2026-05-21"}
    r = client.post("/media/wechat/sync", json=payload, headers=_auth(tokens["admin"]))
    assert r.status_code == 200
    assert r.json()["metrics_upserted"] == 2

    posts = client.get(
        "/media/posts",
        params={"start_date": "2026-05-20", "end_date": "2026-05-21"},
        headers=_auth(tokens["analyst"]),
    )
    assert posts.status_code == 200
    rows = posts.json()
    assert len(rows) == 1
    # Latest snapshot (150), not the sum across both days (230).
    assert rows[0]["read_user_count"] == 150
    assert rows[0]["like_user"] == 5
    assert rows[0]["comment_count"] == 2
    assert rows[0]["collection_user"] == 3

    overview = client.get(
        "/media/overview",
        params={"start_date": "2026-05-20", "end_date": "2026-05-21"},
        headers=_auth(tokens["analyst"]),
    )
    assert overview.status_code == 200
    data = overview.json()
    assert data["posts"] == 1
    assert data["read_user_count"] == 150
    assert data["like_user"] == 5


def test_media_sync_without_account_id_syncs_all_configured_accounts(client, tokens, monkeypatch):
    monkeypatch.delenv("WECHAT_OFFICIAL_APP_ID", raising=False)
    monkeypatch.delenv("WECHAT_OFFICIAL_APP_SECRET", raising=False)
    monkeypatch.setenv("WECHAT_APP_ID_1", "wx-service-a")
    monkeypatch.setenv("WECHAT_APP_SECRET_1", "secret-a")
    monkeypatch.setenv("WECHAT_ACCOUNT_NAME_1", "Service A")
    monkeypatch.setenv("WECHAT_APP_ID_3", "wx-subscription-c")
    monkeypatch.setenv("WECHAT_APP_SECRET_3", "secret-c")
    monkeypatch.setenv("WECHAT_ACCOUNT_NAME_3", "Subscription C")

    import app.views.media.routes as media_view

    class FakeClient:
        def __init__(self, app_id, app_secret):
            self.app_id = app_id
            self.app_secret = app_secret

        def fetch_article_total_rows(self, start_date, end_date):
            return [
                {
                    "external_id": f"{self.app_id}-msg-1",
                    "title": f"Article {self.app_id}",
                    "publish_date": date(2026, 5, 20),
                    "metric_date": date(2026, 5, 20),
                    "url": None,
                    "read_user_count": 8,
                    "share_user_count": 1,
                    "like_user": 0,
                    "comment_count": 0,
                    "collection_user": 0,
                    "read_avg_time": None,
                    "read_user_source": None,
                    "raw_payload": {"app_id": self.app_id},
                }
            ]

    monkeypatch.setattr(media_view, "WeChatOfficialClient", FakeClient)

    payload = {"start_date": "2026-05-20", "end_date": "2026-05-20"}
    r = client.post("/media/wechat/sync", json=payload, headers=_auth(tokens["admin"]))

    assert r.status_code == 200
    data = r.json()
    assert data["accounts_synced"] == 2
    assert data["posts_upserted"] == 2
    assert data["metrics_upserted"] == 2


def test_media_posts_forbidden_for_viewer(client, tokens):
    r = client.get("/media/posts", headers=_auth(tokens["viewer"]))
    assert r.status_code == 403

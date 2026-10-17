"""Exercise collector dispatch with the real HTTP client, without network I/O."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.api_client import APIClient
from app.collector.runner import Target, _upload_one


@pytest.mark.parametrize(
    "platform,content_type,path,form",
    [
        ("xhs", None, "/media/xhs/upload", {"account_id": 7}),
        ("xhs", "overview", "/media/xhs/upload_overview", {"account_id": 7}),
        ("pugongying", None, "/media/pgy/upload", {"account_id": 7}),
        ("channels", None, "/media/channels/upload", {"account_id": 7}),
        ("zhihu", "article", "/media/zhihu/upload", {"content_type": "article"}),
        ("zhihu", "qa", "/media/zhihu/upload", {"content_type": "qa"}),
        ("jd", None, "/upload/", None),
    ],
)
def test_collector_login_and_upload(monkeypatch, platform, content_type, path, form):
    transport = Mock()
    monkeypatch.setattr("app.api_client.requests.Session", lambda: transport)
    login = Mock(status_code=200)
    login.json.return_value = {"access_token": "collector-token"}
    uploaded = Mock(status_code=202 if platform == "jd" else 200)
    uploaded.json.return_value = {"batch_id": 42} if platform == "jd" else {"upserted": 3}
    transport.post.side_effect = [login, uploaded]
    batch = Mock(status_code=200)
    batch.json.return_value = {"status": "completed", "inserted_orders": 3}
    transport.get.return_value = batch

    client = APIClient("https://collector.example/")
    client.login("collector@example.com", "test-password")
    target = Target(platform, Path("unused-session.json"), account_id=7, content_type=content_type)
    result = _upload_one(client, target, b"export", "export.csv")

    assert result["upserted"] == 3
    args, kwargs = transport.post.call_args
    assert args == (f"https://collector.example{path}",)
    assert kwargs["headers"]["Authorization"] == "Bearer collector-token"
    assert kwargs["files"]["file"][:2] == ("export.csv", b"export")
    if form is not None:
        assert kwargs["data"] == form
    else:
        assert kwargs["params"] == {"expected_platform": "jd"}
        assert transport.get.call_args.args == ("https://collector.example/upload/batches/42",)
    client.close()
    transport.close.assert_called_once()

"""Database status must reflect actual schema and data, not fixed success flags."""

from sqlalchemy import text

import app.db as db
from tests.test_api_endpoints import client, tokens, _auth


def test_inventory_counts_and_missing_schema(client, tokens, monkeypatch, tmp_path):
    from app.views.database_status import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    response = client.get("/admin/db-status", headers=_auth(tokens["admin"]))
    assert response.status_code == 200
    body = response.json()
    assert body["health"] == "healthy"
    assert body["counts"]["user"] == 4
    assert body["counts"]["orders"] == 0
    assert "media_article_traffic" in body["counts"]
    assert body["backups"] == {"status": "none", "files": []}
    assert body["analysis_ready"] is False
    # Fault injection is confined to the per-session rpa_test_* database.
    with db.SyncSessionLocal() as session:
        session.execute(
            text("ALTER TABLE orders RENAME COLUMN sku TO sku_test_missing")
        )
        session.execute(
            text("ALTER TABLE media_article_traffic RENAME TO traffic_test_missing")
        )
        session.commit()
    try:
        response = client.get("/admin/db-status", headers=_auth(tokens["admin"]))
        assert response.status_code == 200
        body = response.json()
        assert body["health"] == "attention"
        assert "orders.sku" in body["missing_columns"]
        assert "media_article_traffic" in body["missing_tables"]
        assert body["counts"]["orders"] == 0
        missing = next(
            t for t in body["table_details"] if t["name"] == "media_article_traffic"
        )
        assert missing["rows"] is None and missing["present"] is False
    finally:
        with db.SyncSessionLocal() as session:
            session.execute(
                text("ALTER TABLE orders RENAME COLUMN sku_test_missing TO sku")
            )
            session.execute(
                text("ALTER TABLE traffic_test_missing RENAME TO media_article_traffic")
            )
            session.commit()


def test_backup_inventory_reports_metadata_only(monkeypatch, tmp_path):
    from app.views.database_status import backup_inventory, settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    (tmp_path / "snapshot.sql.gz").write_bytes(b"example backup")
    (tmp_path / ".secret.sql").write_text("secret")
    (tmp_path / "credentials.txt").write_text("secret")
    result = backup_inventory()
    assert result["status"] == "found"
    assert len(result["files"]) == 1
    entry = result["files"][0]
    assert entry["name"] == "snapshot.sql.gz"
    assert entry["bytes"] == 14
    assert set(entry) == {"name", "bytes", "modified_at"}
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "credentials.txt"))
    assert backup_inventory() == {"status": "unavailable", "files": []}


def test_inspection_failure_is_not_reported_as_healthy(client, tokens, monkeypatch):
    import app.views.database_status as status

    async def fail(session):
        raise RuntimeError("private connection details")

    monkeypatch.setattr(status, "inspect_database", fail)
    response = client.get("/admin/db-status", headers=_auth(tokens["admin"]))
    assert response.status_code == 503
    assert "private connection" not in response.text

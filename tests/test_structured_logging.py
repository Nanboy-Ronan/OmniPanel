"""Tests for structured error logging (Item 2).

Verifies that log_exc() attaches context and exc_info to log records,
and that backup.py error paths emit exc_info.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestLogExc:

    def test_message_includes_context_key_value_pairs(self, caplog):
        from app.utils.logger import log_exc
        logger = logging.getLogger("rpa.test.ctx")
        with caplog.at_level(logging.ERROR, logger="rpa.test.ctx"):
            try:
                raise RuntimeError("boom")
            except RuntimeError as exc:
                log_exc(logger, "ingestion_failed", exc, batch_id=42, filename="data.csv")

        record = next(r for r in caplog.records if r.name == "rpa.test.ctx")
        assert "ingestion_failed" in record.message
        assert "batch_id" in record.message
        assert "42" in record.message
        assert "filename" in record.message
        assert "data.csv" in record.message

    def test_exc_info_is_attached(self, caplog):
        from app.utils.logger import log_exc
        logger = logging.getLogger("rpa.test.exc_info")
        with caplog.at_level(logging.ERROR, logger="rpa.test.exc_info"):
            try:
                raise ValueError("detail error")
            except ValueError as exc:
                log_exc(logger, "upload_error", exc)

        record = next(r for r in caplog.records if r.name == "rpa.test.exc_info")
        assert record.exc_info is not None
        assert issubclass(record.exc_info[0], ValueError)
        assert str(record.exc_info[1]) == "detail error"

    def test_works_without_context(self, caplog):
        from app.utils.logger import log_exc
        logger = logging.getLogger("rpa.test.noctx")
        with caplog.at_level(logging.ERROR, logger="rpa.test.noctx"):
            try:
                raise KeyError("missing")
            except KeyError as exc:
                log_exc(logger, "plain_error", exc)

        record = next(r for r in caplog.records if r.name == "rpa.test.noctx")
        assert "plain_error" in record.message
        assert record.exc_info is not None

    def test_log_level_is_error(self, caplog):
        from app.utils.logger import log_exc
        logger = logging.getLogger("rpa.test.level")
        with caplog.at_level(logging.DEBUG, logger="rpa.test.level"):
            try:
                raise Exception("x")
            except Exception as exc:
                log_exc(logger, "test_msg", exc)

        record = next(r for r in caplog.records if r.name == "rpa.test.level")
        assert record.levelno == logging.ERROR


class TestBackupStructuredLogging:

    def test_pg_dump_not_found_logs_exc_info(self, caplog, tmp_path):
        import app.db.backup as backup_mod

        with patch(
            "app.db.backup.subprocess.run",
            side_effect=FileNotFoundError("pg_dump: no such file"),
        ):
            with caplog.at_level(logging.ERROR, logger="app.db.backup"):
                result = backup_mod.backup_database("test", backup_dir=tmp_path)

        assert result is None
        err = next((r for r in caplog.records if r.levelno == logging.ERROR), None)
        assert err is not None, "Expected an ERROR log record from backup_database"
        assert err.exc_info is not None, "Expected exc_info to be attached"

    def test_pg_dump_nonzero_exit_logs_exc_info(self, caplog, tmp_path):
        import app.db.backup as backup_mod

        fake_exc = subprocess.CalledProcessError(1, "pg_dump", stderr="auth failed")
        with patch("app.db.backup.subprocess.run", side_effect=fake_exc):
            with caplog.at_level(logging.ERROR, logger="app.db.backup"):
                result = backup_mod.backup_database("test", backup_dir=tmp_path)

        assert result is None
        err = next((r for r in caplog.records if r.levelno == logging.ERROR), None)
        assert err is not None
        assert err.exc_info is not None

    def test_psql_restore_not_found_logs_exc_info(self, caplog, tmp_path):
        import app.db.backup as backup_mod

        backup_file = tmp_path / "dump.sql"
        backup_file.write_text("-- sql --")

        with patch(
            "app.db.backup.subprocess.run",
            side_effect=FileNotFoundError("psql: not found"),
        ):
            with caplog.at_level(logging.ERROR, logger="app.db.backup"):
                result = backup_mod.restore_database(backup_file)

        assert result is False
        err = next((r for r in caplog.records if r.levelno == logging.ERROR), None)
        assert err is not None
        assert err.exc_info is not None

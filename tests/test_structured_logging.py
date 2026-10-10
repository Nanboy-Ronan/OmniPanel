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


class TestRootLoggingSetup:
    """app.* INFO logs used to vanish under uvicorn: nothing configured the
    root logger. Run in subprocesses so the root logger starts pristine."""

    def _run(self, code: str, **env):
        import os
        import subprocess
        import sys
        from pathlib import Path

        full_env = {**os.environ, **env}
        full_env.pop("LOG_LEVEL", None) if "LOG_LEVEL" not in env else None
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=full_env, capture_output=True, text=True, timeout=60,
        )

    def test_app_main_import_makes_app_info_logs_visible(self):
        proc = self._run(
            "import logging, app.main\n"
            "logging.getLogger('app.scheduler').info('visible-info-line')\n"
            "assert len(logging.getLogger().handlers) == 1\n"
        )
        assert proc.returncode == 0, proc.stderr
        assert "visible-info-line" in proc.stderr
        assert "INFO app.scheduler" in proc.stderr

    def test_log_level_env_is_respected_and_setup_is_idempotent(self):
        proc = self._run(
            "import logging\n"
            "from app.utils.logging_setup import configure_logging\n"
            "configure_logging(); configure_logging()\n"
            "assert len(logging.getLogger().handlers) == 1\n"
            "logging.getLogger('app.x').info('hidden-info')\n"
            "logging.getLogger('app.x').warning('shown-warning')\n",
            LOG_LEVEL="warning",
        )
        assert proc.returncode == 0, proc.stderr
        assert "shown-warning" in proc.stderr
        assert "hidden-info" not in proc.stderr

    def test_existing_root_handler_is_not_duplicated(self):
        proc = self._run(
            "import logging\n"
            "logging.basicConfig()\n"
            "from app.utils.logging_setup import configure_logging\n"
            "configure_logging()\n"
            "assert len(logging.getLogger().handlers) == 1\n"
            "assert logging.getLogger().level == logging.INFO\n"
        )
        assert proc.returncode == 0, proc.stderr

    def test_collector_cli_configures_logging(self):
        proc = self._run(
            "import logging\n"
            "from app.collector import cli\n"
            "try:\n"
            "    cli.main(['--help'])\n"
            "except SystemExit:\n"
            "    pass\n"
            "assert len(logging.getLogger().handlers) == 1\n"
        )
        assert proc.returncode == 0, proc.stderr

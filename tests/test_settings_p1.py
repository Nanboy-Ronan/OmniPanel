"""P1-1 config centralisation — tests written before migration.

All new settings fields must exist in app/config.py with correct defaults
and honour the matching environment variable.

Design boundary: module-level constants (MAX_ATTEMPTS, _TTL, _MAX_UPLOAD_MB)
are migrated to settings because they are fixed at startup.  Functions that
are called at request time and whose tests rely on monkeypatch.setenv for
isolation (wecom_auth, leader.default_lock_path) keep os.getenv so that test
isolation is not broken by the settings singleton.
"""
from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings(**overrides):
    """Instantiate a fresh Settings with the given keyword overrides.

    Passing _env_file=None ensures we read only from the overrides + process
    environment, never from a .env file on disk (avoids test pollution).
    """
    from app.config import Settings
    return Settings(_env_file=None, **overrides)


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimitSettings:
    def test_login_max_attempts_default(self):
        assert _settings().login_max_attempts == 5

    def test_login_lockout_seconds_default(self):
        assert _settings().login_lockout_seconds == 60

    def test_login_max_attempts_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "10")
        assert _settings().login_max_attempts == 10

    def test_login_lockout_seconds_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("LOGIN_LOCKOUT_SECONDS", "120")
        assert _settings().login_lockout_seconds == 120

    def test_rate_limiter_module_uses_settings(self):
        from app.utils.rate_limiter import MAX_ATTEMPTS, WINDOW_SECONDS
        from app.config import settings
        assert MAX_ATTEMPTS == settings.login_max_attempts
        assert WINDOW_SECONDS == settings.login_lockout_seconds


# ─────────────────────────────────────────────────────────────────────────────
# Analysis cache TTL
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheSettings:
    def test_cache_ttl_default(self):
        assert _settings().cache_ttl == 300

    def test_cache_ttl_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("CACHE_TTL", "600")
        assert _settings().cache_ttl == 600

    def test_cache_module_ttl_uses_settings(self):
        from app.utils.cache import _TTL
        from app.config import settings
        assert _TTL == settings.cache_ttl


# ─────────────────────────────────────────────────────────────────────────────
# Leader election lock path
# ─────────────────────────────────────────────────────────────────────────────

class TestLeaderSettings:
    def test_leader_lock_path_default_is_none(self):
        assert _settings().rap_leader_lock_path is None

    def test_leader_lock_path_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("RAP_LEADER_LOCK_PATH", "/tmp/test.lock")
        assert _settings().rap_leader_lock_path == "/tmp/test.lock"


# ─────────────────────────────────────────────────────────────────────────────
# WeCom (Enterprise WeChat) optional config
# ─────────────────────────────────────────────────────────────────────────────

class TestWecomSettings:
    def test_wecom_http_timeout_default(self):
        assert _settings().wecom_http_timeout == pytest.approx(10.0)

    def test_wecom_default_role_default(self):
        assert _settings().wecom_default_role == "viewer"

    def test_wecom_auto_create_users_default(self):
        assert _settings().wecom_auto_create_users is True

    def test_wecom_streamlit_redirect_uri_default_is_none(self):
        assert _settings().wecom_streamlit_redirect_uri is None

    def test_app_url_default_is_none(self):
        assert _settings().app_url is None

    def test_streamlit_url_default_is_none(self):
        assert _settings().streamlit_url is None

    def test_wecom_http_timeout_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("WECOM_HTTP_TIMEOUT", "30")
        assert _settings().wecom_http_timeout == pytest.approx(30.0)

    def test_wecom_default_role_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("WECOM_DEFAULT_ROLE", "analyst")
        assert _settings().wecom_default_role == "analyst"

    def test_wecom_auto_create_users_false_via_env(self, monkeypatch):
        monkeypatch.setenv("WECOM_AUTO_CREATE_USERS", "false")
        assert _settings().wecom_auto_create_users is False

    def test_wecom_streamlit_redirect_uri_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("WECOM_STREAMLIT_REDIRECT_URI", "https://app.example.com/callback")
        assert _settings().wecom_streamlit_redirect_uri == "https://app.example.com/callback"

    def test_app_url_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("APP_URL", "https://app.example.com")
        assert _settings().app_url == "https://app.example.com"


# ─────────────────────────────────────────────────────────────────────────────
# Upload size limit
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadSettings:
    def test_max_upload_mb_default(self):
        assert _settings().max_upload_mb == 50

    def test_max_upload_mb_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_MB", "100")
        assert _settings().max_upload_mb == 100

    def test_ecommerce_upload_module_uses_settings(self):
        from app.views.ecommerce.upload import _MAX_UPLOAD_MB
        from app.config import settings
        assert _MAX_UPLOAD_MB == settings.max_upload_mb

    def test_media_upload_module_uses_settings(self):
        from app.views.media.upload import _MAX_UPLOAD_MB
        from app.config import settings
        assert _MAX_UPLOAD_MB == settings.max_upload_mb

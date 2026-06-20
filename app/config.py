# rap/app/config.py
"""Central application settings loaded from environment variables.

All modules should read configuration from ``settings`` rather than calling
``os.getenv`` directly.  This makes every tuneable value visible in one place
and prevents the scattered boolean-parsing pattern spread across the codebase.
"""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _as_bool(v: str | bool) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    rap_database_url: str = "postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 3600

    # ── Auth ──────────────────────────────────────────────────────────────────
    rap_secret: str = "CHANGE_ME"
    token_lifetime_seconds: int = 86400  # 24 h

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    proxy_headers: bool = True
    forwarded_allow_ips: str = "*"
    ssl_keyfile: str | None = None
    ssl_certfile: str | None = None

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = ""  # comma-separated; empty → localhost fallback

    # ── Timezone ─────────────────────────────────────────────────────────────
    app_timezone: str = "Asia/Shanghai"

    # ── Backup ───────────────────────────────────────────────────────────────
    rpa_backup_dir: str = "backups"
    rap_disable_monthly_backup: bool = False
    # Hour of the day (0-23) in app_timezone at which the daily backup check runs.
    # The actual dump only fires when >= 30 days have elapsed since the last one.
    backup_hour: int = 2

    # ── Upload ───────────────────────────────────────────────────────────────
    max_upload_mb: int = 50

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Rate limiting ─────────────────────────────────────────────────────────
    login_max_attempts: int = 5
    login_lockout_seconds: int = 60

    # ── Analysis cache ────────────────────────────────────────────────────────
    cache_ttl: int = 300

    # ── NL-to-SQL (中文问数据) ────────────────────────────────────────────────
    # Optional Chinese natural-language → SQL helper in the SQL console. Left
    # unconfigured, the feature returns 503 and nothing else is affected.
    #
    # Each provider in the registry (see app/utils/nl_to_sql.PROVIDERS) reads its
    # own API key from one of the fields below; configure as many as you like and
    # users pick provider + model from a dropdown in the SQL console. Keys never
    # leave the server — only the chosen provider id + model travel with a request.
    #
    #   nl_sql_provider — default provider id when the UI doesn't specify one.
    #   nl_sql_model    — default model; used only when it's valid for the chosen
    #                     provider, otherwise that provider's first model is used.
    nl_sql_provider: str = "anthropic"
    nl_sql_model: str | None = None

    # Per-provider API keys (set whichever providers you want available).
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    minimax_api_key: str | None = None
    deepseek_api_key: str | None = None
    moonshot_api_key: str | None = None
    zhipu_api_key: str | None = None
    # Optional base-URL override for the generic "openai" provider only
    # (point it at any other OpenAI-compatible endpoint). Named providers
    # like MiniMax/DeepSeek carry their own base_url in the registry.
    openai_base_url: str | None = None

    # ── Analysis ──────────────────────────────────────────────────────────────
    # Upper bound on the number of raw order rows returned by the old-vs-new
    # breakdown endpoint. Aggregates (counts/sums/daily series) are always
    # computed in SQL; only the optional raw-row preview tables are capped.
    analysis_rows_cap: int = 5000

    # ── Leader election ───────────────────────────────────────────────────────
    rap_leader_lock_path: str | None = None

    # ── WeChat / WeCom ────────────────────────────────────────────────────────
    wechat_sync_timeout: int = 300
    wechat_request_timeout: int = 10

    # ── WeCom (Enterprise WeChat) optional runtime config ─────────────────────
    wecom_http_timeout: float = 10.0
    wecom_default_role: str = "viewer"
    wecom_auto_create_users: bool = True
    wecom_streamlit_redirect_uri: str | None = None
    app_url: str | None = None
    streamlit_url: str | None = None

    # ── WeChat auto-sync scheduler ────────────────────────────────────────────
    # Set WECHAT_AUTO_SYNC_ENABLED=true to enable the daily background sync.
    wechat_auto_sync_enabled: bool = False
    # How many days back to include (WeChat keeps ~180 days; 170 gives a 10-day
    # safety buffer so data is captured before it expires).
    wechat_auto_sync_window_days: int = 170
    # Hour of the day (0-23) in app_timezone at which the sync runs.
    wechat_auto_sync_hour: int = 3

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins:
            return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return ["http://localhost:8501", "https://localhost:8501"]


settings = Settings()

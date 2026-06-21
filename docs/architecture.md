# Architecture

[English](architecture.md) | [中文](architecture.zh-CN.md)

## Overview

```
Streamlit UI  ──HTTP──▶  FastAPI backend  ──▶  PostgreSQL
                              │
                              ├─ ETL:  detect platform → normalize → load
                              ├─ Analytics endpoints (SQL aggregations)
                              ├─ SQL console (read-only ad-hoc queries)
                              └─ NL-to-SQL (optional LLM layer on top of the console)
```

- **Frontend** — Streamlit (`app/ui/`). A thin client: it calls the backend
  over HTTP with a JWT bearer token and renders the JSON it gets back. No
  business logic lives here.
- **Backend** — FastAPI (`app/`). Owns every business rule, the ETL
  pipeline, auth, and the SQL console's safety guardrails.
- **Database** — PostgreSQL, accessed via SQLAlchemy (`asyncpg` for the app,
  sync `psycopg2`/SQLAlchemy for Alembic and some background jobs).
- **Migrations** — Alembic (`alembic/versions/`), driven by `make
  db-upgrade` / `make db-new-migration`.

## Request flow

1. The Streamlit UI sends a request with `Authorization: Bearer <token>`.
2. FastAPI-Users (`app/auth.py`) resolves the token to a `User` row and its
   `role` (`viewer` / `analyst` / `admin`).
3. A dependency (`current_active_user` / `current_analyst_user` /
   `current_admin_user`) gates the endpoint by role.
4. The endpoint does its work (ETL ingestion, an analytics query, or a SQL
   console query) and most mutating/sensitive actions are written to
   `operation_log`.

## Data ingestion (ETL)

Ingestion lives in `app/db/etl/` as three composable stages:

1. **`detect.py` — `detect_platform(df)`**
   Identifies the source platform purely from column-name fingerprints —
   no filename or content sniffing:
   - `买家付款时间` + `收货人手机号/提货人手机号` → `youzan`
   - `京东价` + `客户地址` → `jd`
   - `订单编号` + `收货地址` → `tmall`

   Unrecognized column sets raise `ValueError` and the upload is rejected
   before anything is written.

2. **`normalize.py` — `normalize_dataframe(df)`**
   Maps every platform's raw columns onto one unified schema (order id,
   date, customer key, SKU, quantity, price, receiver, phone, province,
   address, buyer nickname, coupon, distributor). Each platform has
   different raw headers and different null/format conventions for the same
   underlying field; this is where those differences are absorbed so nothing
   downstream needs to know which platform a row came from.

3. **`load.py` — `ingest(df, session)` / `ingest_upload(...)`**
   Persists normalized rows. Order de-duplication is **content-hash based**:
   a row is only inserted if its `(order_id, normalized field values)` hash
   hasn't been seen before, so re-uploading the same export (or an export
   with overlapping date ranges) is always safe — it inserts only genuinely
   new or changed rows. `Customer` rows are upserted by `customer_key`
   (tracking `first_order_date`), and the raw platform-native row is also
   preserved in a per-platform raw table (`youzan_orders` / `jd_orders` /
   `tmall_orders`) for traceability back to the original export.

Self-media ingestion (WeChat, 小红书/XHS, 知乎/Zhihu) follows the same
detect → normalize → load shape but lives under `app/views/media/` and
`app/db/media_etl.py`, since each platform's export shape is different
enough to not share the e-commerce normalizer.

## Data model

Core tables (see `app/db/models.py` for the authoritative definitions):

| Table | Purpose |
|---|---|
| `user` | Accounts; `role` is `viewer`/`analyst`/`admin` |
| `customers` | One row per `customer_key` (de-duplicated identity per platform) |
| `orders` | Unified, normalized order rows across all platforms |
| `upload_batches` | One row per upload; tracks status/counts for the polling UI |
| `upload_rejected_rows` | Rows rejected during normalization, with a reason |
| `youzan_orders` / `jd_orders` / `tmall_orders` | Raw, platform-native rows preserved alongside the normalized `orders` row |
| `media_accounts` | Self-media accounts being tracked (WeChat official accounts, etc.) |
| `media_posts` / `media_post_metrics_daily` | Articles/posts and their daily engagement metrics |
| `media_article_traffic` | Traffic-source breakdown per article |
| `media_sync_runs` | Audit trail of each sync (manual or scheduled), with status and counts |
| `xhs_accounts` / `xhs_posts` | 小红书 (Xiaohongshu) accounts and notes |
| `zhihu_posts` | 知乎 (Zhihu) articles/answers |
| `operation_log` | Append-only audit log of queries and mutating actions |
| `saved_query` | User-saved SQL console queries |

`customer_key` semantics are platform-specific (e.g. phone number on some
platforms, a platform-issued buyer id on others) — see the `SCHEMA_DOC`
constant in `app/utils/nl_to_sql.py` for the full business-rules reference
used by the NL-to-SQL prompt, which doubles as living documentation of these
caveats (including which read metrics are cumulative vs. daily, and how
multi-line orders are de-duplicated).

### Cross-platform customer identity (`app/views/ecommerce/identity.py`)

Because `customer_key` is platform-specific, the same real person ordering
from more than one platform is counted as unrelated customers everywhere
else in this codebase. `GET /analysis/identity/clusters` groups orders
across platforms by recipient phone number instead, in two confidence tiers
that are never summed together:

- **`exact`** — Youzan and Tmall both export full, unmasked phone numbers,
  so two `customer_key`s sharing the same full phone are joined with high
  confidence.
- **`fuzzy`** — JD masks its exported phone numbers (`1******6198` — only
  the first digit and last 4 digits survive), so JD rows can only be
  matched by that partial fingerprint (`app/utils/phone.py`). This produces
  real false positives (any two people sharing the same last 4 digits
  collide), which is why it's kept structurally separate from the exact
  tier in the clustering logic, the API response shape, and the UI
  (跨平台客户 page).

This is an additive, read-only view computed on demand — it does not change
`orders`/`customers` or any other endpoint's behavior.

## Roles & permissions

Three roles, enforced via FastAPI dependencies in `app/auth.py`:

| Role | Can do |
|---|---|
| `viewer` | Read analytics dashboards |
| `analyst` | Everything `viewer` can, plus: upload files, use the SQL console and NL-to-SQL |
| `admin` | Everything `analyst` can, plus: manage users/roles, clear the database, manage media accounts |

The first user ever registered is auto-promoted to `admin`
(`app/auth.py:UserManager.on_after_register`); every later registration
defaults to `viewer`.

## API surface

Routers are mounted in `app/main.py`. Grouped by domain:

| Prefix | Domain | Notes |
|---|---|---|
| `/auth/jwt`, `/auth/register`, `/auth/wecom` | Auth | JWT login, self-registration, Enterprise WeChat (WeCom) OAuth |
| `/upload` | E-commerce ingestion | Upload a file; poll `upload_batches/{id}` for status |
| `/analysis` | E-commerce analytics | Overview, customer breakdowns, repurchase rate, cohort retention (`/analysis/cohort_retention`), cross-platform customer identity (`/analysis/identity/clusters`), field coverage, the SQL console (`/analysis/sql`) and NL-to-SQL (`/analysis/nl-sql`) |
| `/orders_all` | E-commerce | Raw order listing/export |
| `/media`, `/media/xhs`, `/media/zhihu` | Self-media | Accounts, posts, metrics, traffic, WeChat sync trigger |
| `/admin` | Admin | User management, `/admin/clear-db` |
| `/saved-queries` | SQL console | Save/list/delete a user's saved queries |
| `/health`, `/ping` | Ops | Liveness/readiness for a reverse proxy or monitoring |

## SQL console safety model

The ad-hoc SQL console (`POST /analysis/sql`) and NL-to-SQL both funnel
through the same guardrails before anything touches the database:

1. **Statement allow-list** — only `SELECT`/`WITH` statements are accepted;
   anything else is rejected before execution.
2. **Automatic `LIMIT`** — a `LIMIT` is injected if the query doesn't
   already have one, bounded by `analysis_rows_cap`.
3. **Read-only transaction** — the query runs under `SET LOCAL
   transaction_read_only = on`, so even a clever injection that bypasses the
   allow-list cannot mutate data.
4. **Statement timeout** — long-running queries are killed server-side.
5. **Audit logging** — every query (and its caller, role, and result count)
   is written to `operation_log`.

NL-to-SQL is a thin layer in front of this: it only ever *generates* the SQL
text from a question; the generated SQL is executed through the exact same
pipeline above, so a misbehaving LLM response is no more dangerous than a
human typing a bad query into the console. See
[中文问数据 (NL-to-SQL)](nl-to-sql.md) for the provider registry and how
generation works.

## Background jobs

Started from the FastAPI `lifespan` in `app/main.py`, gated by a leader
election (`app/utils/leader.py`) so only one backend process runs them even
when scaled horizontally:

- **Monthly backup loop** (`app/scheduler.py:monthly_backup_loop`) — dumps
  the database on a schedule unless `RAP_DISABLE_MONTHLY_BACKUP=true`.
- **WeChat auto-sync loop** (`app/scheduler.py:wechat_auto_sync_loop`) —
  see [WeChat auto-sync](wechat-auto-sync.md) for why this exists and how
  it's configured.

## Configuration reference

All settings are environment variables, loaded via `pydantic_settings` in
`app/config.py` (and optionally from a `.env` file). `.env.example`
documents the commonly-changed ones inline; the full set, with defaults:

| Variable | Default | Purpose |
|---|---|---|
| `RAP_DATABASE_URL` | `postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa` | Main database connection |
| `DB_ECHO` | `false` | Log every SQL statement (debugging) |
| `DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `20` | Extra connections allowed beyond pool size |
| `DB_POOL_RECYCLE` | `3600` | Seconds before a pooled connection is recycled |
| `RAP_SECRET` | `CHANGE_ME` | Signs auth tokens — **must** be changed in any real deployment |
| `TOKEN_LIFETIME_SECONDS` | `86400` | JWT lifetime (24h) |
| `HOST` | `0.0.0.0` | uvicorn bind address |
| `PORT` | `8000` | uvicorn bind port |
| `PROXY_HEADERS` | `true` | Trust `X-Forwarded-*` from a reverse proxy |
| `FORWARDED_ALLOW_IPS` | `*` | Which proxy IPs to trust for forwarded headers |
| `SSL_KEYFILE` / `SSL_CERTFILE` | unset | Enable HTTPS directly in uvicorn (see [Getting started](getting-started.md)) |
| `CORS_ORIGINS` | unset (falls back to `localhost:8501`) | Comma-separated allowed origins |
| `APP_TIMEZONE` | `Asia/Shanghai` | Used for logging and all scheduler timing |
| `RPA_BACKUP_DIR` | `backups` | Directory for database dump files |
| `RAP_DISABLE_MONTHLY_BACKUP` | `false` | Disable the background backup loop |
| `BACKUP_HOUR` | `2` | Hour (0–23, `APP_TIMEZONE`) the daily backup check runs |
| `MAX_UPLOAD_MB` | `50` | Max accepted upload file size |
| `REDIS_URL` | `redis://localhost:6379/0` | Optional — distributed rate limiting |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed logins before lockout |
| `LOGIN_LOCKOUT_SECONDS` | `60` | Lockout duration |
| `CACHE_TTL` | `300` | Analytics endpoint result cache TTL (seconds) |
| `NL_SQL_PROVIDER` | `anthropic` | Default NL-to-SQL provider id |
| `NL_SQL_MODEL` | unset | Default model (falls back to the provider's first model if invalid) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `MINIMAX_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, `ZHIPU_API_KEY` | unset | Per-provider keys — configure any subset you want available |
| `OPENAI_BASE_URL` | unset | Override base URL for the generic `openai` provider only |
| `ANALYSIS_ROWS_CAP` | `5000` | Max raw rows returned by raw-row preview endpoints (aggregates are uncapped) |
| `RAP_LEADER_LOCK_PATH` | unset | File path used for leader election across multiple backend processes |
| `WECHAT_SYNC_TIMEOUT` | `300` | Timeout for a full WeChat sync run |
| `WECHAT_REQUEST_TIMEOUT` | `10` | Timeout per WeChat API call |
| `WECOM_HTTP_TIMEOUT` | `10.0` | Timeout for Enterprise WeChat (WeCom) API calls |
| `WECOM_DEFAULT_ROLE` | `viewer` | Role assigned to users auto-created via WeCom OAuth |
| `WECOM_AUTO_CREATE_USERS` | `true` | Auto-create a local user on first WeCom login |
| `WECOM_STREAMLIT_REDIRECT_URI` | unset | Where WeCom OAuth redirects back to after login |
| `APP_URL` / `STREAMLIT_URL` | unset | Used to build absolute links in some flows |
| `WECHAT_AUTO_SYNC_ENABLED` | `false` | Enable the daily background WeChat sync |
| `WECHAT_AUTO_SYNC_WINDOW_DAYS` | `170` | Days of history covered per run |
| `WECHAT_AUTO_SYNC_HOUR` | `3` | Hour (0–23, `APP_TIMEZONE`) the sync runs |

WeChat/WeCom per-account credentials (`WECHAT_APP_ID_N`,
`WECHAT_APP_SECRET_N`, `WECHAT_ACCOUNT_NAME_N`, `WECOM_CORP_ID`,
`WECOM_AGENT_ID`, `WECOM_APP_SECRET`) are also environment variables — see
`.env.example` for the numbered-account pattern used when multiple accounts
are connected.

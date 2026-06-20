# OmniPanel

[![CI](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml/badge.svg)](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Nanboy-Ronan/OmniPanel)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)

[English](README.md) | [中文](README.zh-CN.md)

A self-hosted analytics platform for Chinese e-commerce and self-media data —
built on the **official data exports** each platform already gives you, with no
scraping involved.

Upload the spreadsheets you export from your store and content back-ends,
OmniPanel normalizes them into a clean schema, and you get customer analytics,
content-performance metrics, and an ad-hoc SQL console (including a
natural-language "ask your data" helper) on top.

## Why

General-purpose BI tools don't understand platform-specific semantics (what a
"customer" is on each marketplace, which read metrics are cumulative, how to
de-duplicate multi-line orders). Scraper-based tools live in a legal grey area
and break constantly. OmniPanel sits in between: it ingests the **authoritative
exports** you already own and encodes the platform business rules so the numbers
are correct out of the box.

## Features

- **Multi-platform order ingestion** — drop in exports from common marketplaces;
  column fingerprinting auto-detects the platform and normalizes to one schema.
- **Customer analytics** — new vs. returning breakdowns, repurchase rate and
  time-to-repurchase, per-customer order history, regional distribution.
- **Self-media analytics** — article/post metrics for owned official accounts
  and content platforms, plus a content→sales impact view.
- **SQL console** — a read-only ad-hoc query tool with strict guardrails
  (SELECT/WITH only, auto `LIMIT`, statement timeout, full audit logging).
- **中文问数据 (NL-to-SQL)** — ask a question in plain Chinese and get generated
  SQL + results. Pluggable LLM providers (Anthropic, OpenAI, MiniMax, DeepSeek,
  Moonshot, Zhipu); API keys stay server-side and you pick provider/model from a
  dropdown.
- **Roles & audit** — viewer / analyst / admin roles; every query and mutating
  action is written to an operation log.

## Architecture

```
Streamlit UI  ──HTTP──▶  FastAPI backend  ──▶  PostgreSQL
                              │
                              ├─ ETL: detect → normalize → load
                              ├─ analytics endpoints
                              └─ SQL console + NL-to-SQL
```

- **Backend:** FastAPI (`app/`), SQLAlchemy + asyncpg, Alembic migrations.
- **Frontend:** Streamlit (`app/ui/`), talking to the backend over HTTP.
- **Database:** PostgreSQL.

## Quick start

Requirements: Python 3.13+ and a PostgreSQL instance.

```bash
# 1. Install dependencies
python -m pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
#    edit .env: set RAP_DATABASE_URL, RAP_SECRET, and (optionally) an LLM key

# 3. Apply database migrations
make db-upgrade            # or: alembic upgrade head

# 4. Run the backend (FastAPI on :8000)
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5. Run the frontend (Streamlit on :8501) in another shell
streamlit run app/ui/dashboard.py
```

Then open the Streamlit URL, register the first user (becomes admin), and start
uploading exports.

## Configuration

All settings come from environment variables (see `.env.example` for the full
list). The essentials:

| Variable | Purpose |
|---|---|
| `RAP_DATABASE_URL` | PostgreSQL connection (`postgresql+asyncpg://…`) |
| `RAP_SECRET` | Secret used to sign auth tokens — set a strong random value |
| `CORS_ORIGINS` | Comma-separated allowed origins for the API |

### Enabling 中文问数据 (NL-to-SQL)

Optional. Configure an API key for any provider(s) you want; users then pick
provider + model from a dropdown in the SQL console. Keys never leave the server.

```bash
NL_SQL_PROVIDER=minimax            # default provider
MINIMAX_API_KEY=...                # or ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / ...
```

With no key configured, the feature simply returns 503 and nothing else is
affected.

## Documentation

- [Getting started](docs/getting-started.md) — install, configure, run, first admin user
- [Architecture](docs/architecture.md) — components, data model, ETL pipeline, roles, API surface
- [中文问数据 (NL-to-SQL)](docs/nl-to-sql.md) — how it works, the provider registry, adding a provider
- [Testing](docs/testing.md) — running the suite, the synthetic dataset, real-file smoke tests
- [WeChat auto-sync](docs/wechat-auto-sync.md) — daily background sync for official-account metrics

## Testing

The test suite needs a reachable PostgreSQL server. It never touches your app
database — it creates and drops throwaway `*_test_*` databases on the same
server.

```bash
# Point the tests at a server (credentials only; a temp DB is created per run)
export PG_TEST_URL=postgresql://user:pass@127.0.0.1:5432/postgres
make test                  # or: pytest -q
```

## Database migrations

Schema changes are managed with Alembic. Common targets:

```bash
make db-upgrade                          # apply all pending migrations
make db-new-migration msg="add table x"  # autogenerate from ORM changes
make db-check                            # verify DB is at head
```

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

See [LICENSE](LICENSE).

# OmniPanel

[![CI](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml/badge.svg)](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Nanboy-Ronan/OmniPanel)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)

[English](README.md) | [中文](README.zh-CN.md)

General-purpose BI (business intelligence) tools draw charts on whatever
schema you hand them — they don't know what a "customer" means on your
platform, which metrics are cumulative, or how multi-line records should be
de-duplicated. That correctness work gets redone by hand in every dashboard,
every query, forever. OmniPanel takes the opposite approach: encode the
business rules once, in the platform itself, so every dashboard, every SQL
query, and every natural-language question built on top of it is correct by
construction — not just another chart-on-top-of-SQL tool.

The current proof point is Chinese e-commerce and self-media data, where
those rules are sharp and well understood: upload the **official exports**
you already get from your store and content back-ends — no scraping — and
OmniPanel normalizes them, encodes the platform-specific business rules
(customer identity, repurchase windows, which metrics are cumulative vs.
daily, de-duplication), and gives you dashboards, cross-platform analytics, a
governed SQL console, and a natural-language "ask your data" layer on top.
It's where we started, not where we stop — the same ingest → normalize →
encode-business-rules → analyze pipeline is built to extend to other data
domains over time.

## Screenshots

*(all data below is a randomly generated synthetic dataset, not a real store)*

| Customer analytics | Cohort retention |
|---|---|
| ![Customer analytics overview](docs/images/screenshot_analysis.png) | ![Monthly cohort retention curves](docs/images/screenshot_cohort.png) |

| Cross-platform customer identity | SQL console |
|---|---|
| ![Cross-platform customer identity](docs/images/screenshot_identity.png) | ![SQL console with query results](docs/images/screenshot_sql.png) |

## Why official exports, not scraping

Scraper-based tools live in a legal grey area and break every time a platform
redesigns its page or tightens anti-bot defenses. OmniPanel only ingests the
**authoritative exports** you already legally own — official, structured,
stable — and spends its effort on the correctness work generic BI tools skip
instead of on fighting anti-bot measures.

### How this compares to similar projects

| Project | Data source | What it actually ships |
|---|---|---|
| **OmniPanel** (this repo) | Official exports (Youzan/JD/Tmall, WeChat OA/XHS/Zhihu) | Self-hosted app: dashboards, cohort/identity analytics, SQL console, NL-to-SQL |
| [DA_Multi_Agent_Workflow](https://github.com/liuchaoqi-7/DA_Multi_Agent_Workflow) | Platform APIs + crawlers (Douyin Shop, XHS, WeChat Video Channels, ad platforms) | n8n-orchestrated multi-agent ETL/analytics pipeline synced into Feishu |
| [ECommerceCrawlers](https://github.com/DropsDevopsOrg/ECommerceCrawlers) | Web scraping (Taobao, Xianyu, Weibo, 20+ sites) | Scraper code samples / learning exercises, not a deployable product |
| [data-api (Just One API)](https://github.com/justoneapi/data-api) | Web scraping, 40+ platforms | Hosted pay-per-call data feed, no analytics layer |
| [bodapi global-ecommerce-data-scraping-solutions-cn](https://github.com/bodapi/global-ecommerce-data-scraping-solutions-cn) | Web scraping with anti-bot bypass, 20+ global platforms | Hosted cross-border price/review/competitive-intelligence feed |

This isn't "we're better at everything" — see
[docs/comparison.md](docs/comparison.md) for what each of those projects does
better (and which of those ideas are worth borrowing into OmniPanel) alongside
what OmniPanel does that none of them do.

## Features

- **Multi-platform e-commerce ingestion** — drop in order exports from
  有赞 (Youzan), 京东 (JD), or 天猫 (Tmall); column fingerprinting
  auto-detects the platform and normalizes everything to one schema, while
  the platform-native row is preserved alongside it for traceability.
- **Customer analytics** — new vs. returning breakdowns, repurchase rate and
  time-to-repurchase, per-customer order history, regional distribution,
  monthly cohort retention curves, and cross-platform customer identity
  matching (the same person ordering from Youzan/JD/Tmall is recognized as
  one customer by phone number instead of three, with explicit
  exact/fuzzy-confidence tiers since JD masks its phone numbers).
- **Self-media analytics** — daily article/post metrics for 微信公众号
  (WeChat Official Accounts, auto-synced via the WeChat API), 小红书
  (Xiaohongshu), and 知乎 (Zhihu), plus a content→sales impact view that
  correlates publish dates with order volume.
- **SQL console** — a read-only ad-hoc query tool with strict guardrails
  (SELECT/WITH only, auto `LIMIT`, statement timeout, full audit logging),
  with the ability to save and share frequently-used queries.
- **中文问数据 (NL-to-SQL)** — ask a question in plain Chinese and get generated
  SQL + results. Pluggable LLM providers (Anthropic, OpenAI, MiniMax, DeepSeek,
  Moonshot, Zhipu); API keys stay server-side and you pick provider/model from a
  dropdown.
- **Roles, SSO & audit** — viewer / analyst / admin roles, plus optional
  Enterprise WeChat (WeCom) single sign-on; every query and mutating action
  is written to an operation log, and admins get a Users screen for
  account/role management.
- **Background jobs** — scheduled WeChat metric sync and monthly database
  backups, leader-elected so they stay safe to run with multiple backend
  workers.

## Architecture

![OmniPanel architecture](docs/images/architecture.png)

- **Backend:** FastAPI (`app/`) — auth (JWT + WeCom SSO), the ETL pipeline,
  analytics endpoints, the SQL console + NL-to-SQL, and leader-elected
  background jobs (WeChat sync, monthly DB backup).
- **Frontend:** Streamlit (`app/ui/`), talking to the backend over HTTP.
- **Database:** PostgreSQL, with an optional Redis layer for shared caching
  and login rate-limiting.

See [Architecture](docs/architecture.md) for the full diagrams (backend
internals, the WeCom SSO flow, optional Redis/NL-to-SQL layers) and the
complete API surface.

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
- [Comparison with similar projects](docs/comparison.md) — honest pros/cons vs. scraper-based and agent-workflow alternatives

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

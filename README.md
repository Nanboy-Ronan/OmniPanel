# OmniPanel

**Business-rule-correct analytics on official platform exports** — dashboards, cohort retention, cross-platform customer identity, and natural-language SQL in one self-hosted app.

[![CI](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml/badge.svg)](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/github/license/Nanboy-Ronan/OmniPanel)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-13%2B-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[English](README.md) | [中文](README.zh-CN.md)

---

## Table of Contents

- [What is OmniPanel?](#what-is-omnipanel)
- [Screenshots](#screenshots)
- [Features](#features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Community & Support](#community--support)
- [License](#license)

---

## What is OmniPanel?

General-purpose BI tools draw charts on whatever schema you hand them. They don't know what a "customer" means on your platform, which metrics are cumulative, or how multi-line records should be de-duplicated. That correctness work gets redone by hand in every dashboard, every query, forever.

OmniPanel takes the opposite approach: **encode the business rules once, in the platform itself**, so every dashboard, SQL query, and natural-language question built on top is correct by construction.

The current proof point is Chinese e-commerce and self-media data. Upload the **official exports** you already get from your business and content back-ends — no scraping — and OmniPanel normalizes them, encodes the platform-specific business rules (customer identity, repurchase windows, which metrics are cumulative vs. daily, de-duplication), and gives you dashboards, cross-platform analytics, a governed SQL console, and a natural-language "ask your data" layer on top. It's where we started, not where we stop — the same ingest → normalize → encode-business-rules → analyze pipeline is built to extend to other data domains over time.

## Screenshots

> All data below is a randomly generated synthetic dataset, not a real business.

| Customer analytics | Cohort retention |
|---|---|
| ![Customer analytics overview](docs/images/screenshot_analysis.png) | ![Monthly cohort retention curves](docs/images/screenshot_cohort.png) |

| Cross-platform customer identity | SQL console |
|---|---|
| ![Cross-platform customer identity](docs/images/screenshot_identity.png) | ![SQL console with query results](docs/images/screenshot_sql.png) |

## Features

### Data ingestion

- **Multi-platform e-commerce** — drop in order exports from 有赞 (Youzan), 京东 (JD), or 天猫 (Tmall); column fingerprinting auto-detects the platform and normalizes everything to one schema, while preserving the platform-native row alongside it for traceability.
- **Self-media** — daily article/post metrics for 微信公众号 (WeChat Official Accounts, auto-synced via the WeChat API), 小红书 (Xiaohongshu), and 知乎 (Zhihu), plus a content → sales impact view that correlates publish dates with order volume.

### Analytics

- **Customer analytics** — new vs. returning breakdowns, repurchase rate and time-to-repurchase, per-customer order history, regional distribution, and monthly cohort retention curves.
- **Cross-platform customer identity** — the same person ordering from Youzan/JD/Tmall is recognized as one customer by phone number instead of three, with explicit exact/fuzzy-confidence tiers (JD masks its phone numbers; the fuzzy tier uses a partial fingerprint and is kept structurally separate from the exact tier to avoid false positives).

### Query layer

- **SQL console** — read-only ad-hoc queries with strict guardrails: SELECT/WITH only, auto `LIMIT`, statement timeout, full audit logging, and save-and-share for frequently-used queries.
- **中文问数据 (NL-to-SQL)** — ask a question in plain Chinese and get generated SQL + results. Pluggable LLM providers (Anthropic, OpenAI, MiniMax, DeepSeek, Moonshot, Zhipu); API keys stay server-side and you pick provider/model from a dropdown.

### Security & operations

- **Roles, SSO & audit** — viewer / analyst / admin roles; optional Enterprise WeChat (WeCom) single sign-on; every query and mutating action is written to an operation log; admins get a Users screen for account/role management.
- **Background jobs** — scheduled WeChat metric sync and monthly database backups, leader-elected so they stay safe to run with multiple backend workers.

### Why official exports, not scraping

Scraper-based tools live in a legal grey area and break every time a platform redesigns its page or tightens anti-bot defenses. OmniPanel only ingests the **authoritative exports** you already legally own — official, structured, stable — and spends its effort on the correctness work generic BI tools skip.

<details>
<summary>How this compares to similar projects</summary>

| Project | Data source | What it actually ships |
|---|---|---|
| **OmniPanel** (this repo) | Official exports (Youzan/JD/Tmall, WeChat OA/XHS/Zhihu) | Self-hosted app: dashboards, cohort/identity analytics, SQL console, NL-to-SQL |
| [DA_Multi_Agent_Workflow](https://github.com/liuchaoqi-7/DA_Multi_Agent_Workflow) | Platform APIs + crawlers (Douyin Shop, XHS, WeChat Video Channels, ad platforms) | n8n-orchestrated multi-agent ETL/analytics pipeline synced into Feishu |
| [ECommerceCrawlers](https://github.com/DropsDevopsOrg/ECommerceCrawlers) | Web scraping (Taobao, Xianyu, Weibo, 20+ sites) | Scraper code samples / learning exercises, not a deployable product |
| [data-api (Just One API)](https://github.com/justoneapi/data-api) | Web scraping, 40+ platforms | Hosted pay-per-call data feed, no analytics layer |
| [bodapi global-ecommerce-data-scraping-solutions-cn](https://github.com/bodapi/global-ecommerce-data-scraping-solutions-cn) | Web scraping with anti-bot bypass, 20+ global platforms | Hosted cross-border price/review/competitive-intelligence feed |

See [docs/comparison.md](docs/comparison.md) for what each project does better and what OmniPanel does that none of them do.

</details>

## Architecture

![OmniPanel architecture](docs/images/architecture.png)

| Layer | Technology | Role |
|---|---|---|
| Frontend | Streamlit (`app/ui/`) | Thin client — renders backend responses; no business logic |
| Backend | FastAPI (`app/`) | Auth, ETL pipeline, analytics, SQL console, background jobs |
| Database | PostgreSQL + SQLAlchemy | Unified normalized schema; raw platform rows preserved alongside |
| Cache / rate limiting | Redis (optional) | Distributed cache and login rate limiter; falls back to in-process if absent |

See [Architecture](docs/architecture.md) for full diagrams (backend internals, WeCom SSO flow, optional Redis/NL-to-SQL layers) and the complete API surface.

## Quick start

Requirements: Python 3.13+ and a PostgreSQL instance (13+).

```bash
# 1. Clone and install
git clone https://github.com/Nanboy-Ronan/OmniPanel.git
cd OmniPanel
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

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

Open the Streamlit URL, register the first user (automatically becomes admin), and start uploading exports. Full walkthrough in [Getting started](docs/getting-started.md).

## Configuration

All settings come from environment variables (see `.env.example` for the full list). The essentials:

| Variable | Purpose |
|---|---|
| `RAP_DATABASE_URL` | PostgreSQL connection string (`postgresql+asyncpg://…`) |
| `RAP_SECRET` | Signs auth tokens — use a strong random value (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `RAP_SECRET_PREVIOUS` | Comma-separated previous `RAP_SECRET` values, accepted only for verifying already-issued tokens during a rotation window |
| `FORWARDED_ALLOW_IPS` | Comma-separated IPs/networks trusted to set `X-Forwarded-For` when the API sits behind a reverse proxy (default `127.0.0.1`) |
| `CORS_ORIGINS` | Comma-separated allowed origins for the API |

### Rotating `RAP_SECRET`

`RAP_SECRET` signs every JWT (login sessions) plus password-reset and email-verification tokens. Rotating it without `RAP_SECRET_PREVIOUS` immediately logs out every signed-in user. To rotate without a forced mass logout:

1. Generate a new secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Move the current `RAP_SECRET` value into `RAP_SECRET_PREVIOUS`, then set `RAP_SECRET` to the new value, and restart the backend. New logins are signed with `RAP_SECRET`; existing sessions signed with the old secret keep verifying because it's now in `RAP_SECRET_PREVIOUS`.
3. Wait at least `TOKEN_LIFETIME_SECONDS` (default 86400s / 24h) so every token signed with the old secret has expired, then remove `RAP_SECRET_PREVIOUS` and restart once more.

If the old secret is suspected leaked rather than rotated on a routine schedule, skip step 3's wait and consider force-expiring sessions some other way.

### Enabling 中文问数据 (NL-to-SQL)

Optional. Configure an API key for any provider; users then pick provider + model from a dropdown. Keys never leave the server.

```dotenv
NL_SQL_PROVIDER=minimax            # default provider
MINIMAX_API_KEY=...                # or ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / ...
```

With no key configured, the feature returns 503 and nothing else is affected.

## Documentation

| Doc | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Install, configure, run, create the first admin user |
| [Architecture](docs/architecture.md) | Components, data model, ETL pipeline, roles, API surface, configuration reference |
| [中文问数据 (NL-to-SQL)](docs/nl-to-sql.md) | How it works, the provider registry, adding a provider |
| [Testing](docs/testing.md) | Running the suite, the synthetic dataset, skipped tests |
| [WeChat auto-sync](docs/wechat-auto-sync.md) | Daily background sync for official-account metrics |
| [Dependency maintenance](docs/maintenance.md) | Upgrade cadence, critical-package list, the bump→test→smoke process |
| [Comparison with similar projects](docs/comparison.md) | Honest pros/cons vs. scraper-based and agent-workflow alternatives |

## Roadmap

Items we're working on or expect to tackle next:

- **Docker / docker-compose deployment** — a fully containerized path from `git clone` to running app
- **Douyin Shop & WeChat Video Channels connectors** — two large platforms currently without a stable official export
- **Feishu / DingTalk push** — send saved-query results to where your team already works

Under consideration (open a [Discussion](https://github.com/Nanboy-Ronan/OmniPanel/discussions) to weigh in):

- Multi-step NL-to-SQL with agent routing for ambiguous or multi-hop questions
- Data warehouse layering (ODS → DWD → DIM → ADS staging areas)
- Usage visualization on the admin operation-log page

## Contributing

Issues and PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for:

- How to set up a local development environment
- Code style and commit message conventions
- How to add a new platform connector or NL-to-SQL provider
- The PR checklist

## Community & Support

- **Questions & ideas** — [GitHub Discussions](https://github.com/Nanboy-Ronan/OmniPanel/discussions)
- **Bugs & feature requests** — [GitHub Issues](https://github.com/Nanboy-Ronan/OmniPanel/issues)
- **Security vulnerabilities** — see [SECURITY.md](SECURITY.md) (please don't open a public issue)

## License

OmniPanel is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

You can use, modify, and self-host OmniPanel freely. If you distribute a modified version — or run one as a network service — you must make the source code available under the same license.

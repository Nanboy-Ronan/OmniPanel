# OmniPanel

**A self-hosted workspace for commerce, content, and customer analytics.**

Bring orders, content performance, and creator partnerships into one place. Import platform exports or connect a collector, explore the results, and share weekly reports—with data coverage and metric definitions visible alongside the numbers.

[![CI](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml/badge.svg)](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

**English** · [简体中文](README.zh-CN.md)

[Try the preview](#try-the-preview) · [Supported platforms](#supported-platforms) · [Self-host](#self-host) · [Documentation](#documentation)

![OmniPanel business overview: revenue trends, channel mix, and data coverage](docs/images/screenshot_overview.png)

*Every screenshot uses synthetic demo data. No production accounts, credentials, or business exports are included.*

## From platform exports to useful answers

OmniPanel is built for teams that sell across marketplaces and publish across content platforms. It handles platform-specific import formats, order normalization, duplicate detection, and analytics in the same application.

| Your question | Where to start |
|---|---|
| How are sales changing, and which channels or products contribute? | Commerce dashboard with period comparisons, channel mix, product rankings, and regional breakdowns |
| Who returns, and how often? | Customer profiles, monthly retention cohorts, and cross-platform matching with separate exact and fuzzy results |
| Which content gets attention and engagement? | Content distributions, publishing trends, account metrics, and audience sources |
| How are creator partnerships performing? | Pugongying campaign and creator comparisons, spend, reach, and interaction costs |
| What changed this week? | Saved weekly reports across commerce and content, with coverage warnings and optional commentary |
| Can I investigate further? | Read-only SQL, natural-language queries, or scoped WorkBuddy tools |

Revenue follows shared order-status and refund rules. Dashboards distinguish data coverage from import time; incomplete periods remain visible. Content-to-sales views show associations, while exact and fuzzy customer matches remain separate.

## Explore the workspace

| Commerce analytics | Content performance |
|---|---|
| ![Commerce analytics](docs/images/screenshot_analysis.png) | ![Content performance](docs/images/screenshot_content.png) |

| Creator partnerships | WorkBuddy connection |
|---|---|
| ![Pugongying partnerships](docs/images/screenshot_pgy.png) | ![WorkBuddy connection using synthetic data](docs/images/screenshot_workbuddy.png) |

<details>
<summary>Customer retention, identity matching, SQL, and operations</summary>

| Customer retention | Cross-platform customers |
|---|---|
| ![Monthly retention cohorts](docs/images/screenshot_cohort.png) | ![Customer identity matching](docs/images/screenshot_identity.png) |

| SQL console | Database health |
|---|---|
| ![SQL console](docs/images/screenshot_sql.png) | ![Database status](docs/images/screenshot_database.png) |

![Audit log with synthetic accounts and events](docs/images/screenshot_logs.png)

</details>

## Try the preview

Explore the interface locally with synthetic data. This preview needs **Node.js 22.18+**, with no database or Enterprise WeChat credentials.

```sh
git clone https://github.com/Nanboy-Ronan/OmniPanel.git
cd OmniPanel
npm --prefix frontend ci
npm --prefix frontend run build
node frontend/scripts/preview-fixtures.mjs
```

Open **http://127.0.0.1:5180/console/** and use the simulated sign-in. The preview serves fixture responses; uploads, authorization, and other backend operations are not a live deployment. The application interface is primarily Chinese.

## Supported platforms

| Platform | Data | Ingestion |
|---|---|---|
| Youzan · Tmall | Orders and customers | Upload platform exports |
| JD | Orders and customers | Upload exports or collect the merchant portal's rendered order list |
| WeChat Official Accounts | Articles, traffic, and followers | Scheduled API synchronization |
| Xiaohongshu | Notes, engagement, account metrics, and audience sources | Upload exports or use browser-based collection; availability varies by dataset |
| WeChat Channels · Zhihu | Content and performance | Upload exports or use browser-based collection |
| Pugongying | Creator collaborations and campaign performance | Upload exports or use browser-based collection |

Imports recognize supported column layouts and normalize records for analysis. Repeated files and orders are deduplicated; later exports can refresh order statuses. Browser collectors use authorized platform sessions and may need login renewal or updates when portal pages change. See the [collector guide](docs/collector.md).

## WorkBuddy and AI queries

**WorkBuddy connection.** Analysts and administrators grant scoped access through OAuth or personal tokens. Read-only tools return bounded analytics and media detail, with query auditing and revocation. They do not expose customer identity fields, collector credentials, or arbitrary SQL.

**Weekly report generation.** An optional WorkBuddy provider processes aggregate snapshots through a job queue, validates evidence references, and supports retries and WeCom delivery. Account owners can also authorize local browser login repair for supported collectors.

**Natural-language SQL.** Ask a question in Chinese, inspect the generated SQL, and query restricted reporting views. A dedicated database login enforces read-only access.

These integrations require configuration. External AI features are opt-in and send their configured inputs to the selected provider. See [WorkBuddy setup](docs/workbuddy-integration.md) and [NL-to-SQL](docs/nl-to-sql.md).

## Self-host

The application uses **React + TypeScript**, **FastAPI**, and **PostgreSQL**. Docker Compose runs the database, migrations, API, and Nginx-served frontend.

From the repository root:

```sh
cp .env.example .env
# Configure .env before starting the application.
docker compose up --build
```

Before signing in, configure:

| Setting | Purpose |
|---|---|
| `RAP_SECRET` | A long, randomly generated authentication signing secret |
| `WECOM_CORP_ID`, `WECOM_AGENT_ID`, `WECOM_APP_SECRET` | Your Enterprise WeChat application credentials |
| `WECOM_CONSOLE_REDIRECT_URI` | Your reachable `/console/` callback URL, registered with WeCom |
| `PUBLIC_BASE_URL` | Your public deployment origin for authorization and report links |

The frontend listens on **http://localhost:5173/console/**. Real sign-in requires a WeCom-compatible callback setup; use the preview for a credential-free tour. The first authenticated user becomes an administrator; subsequent new users await approval by default.

Compose supplies its own database connection and persists database and backup volumes. For a public deployment, configure HTTPS, database credentials, and trusted proxy settings for your environment. SQL and NL-to-SQL additionally require `RAP_SQL_CONSOLE_DATABASE_URL`; without it, those query features remain disabled.

- **First installation:** [Getting started](docs/getting-started.md)
- **Existing installation:** [Upgrade notes](docs/upgrading.md)—apply migrations through `0026` before starting this version
- **All settings:** [.env.example](.env.example)

## Built to operate

Viewer, analyst, and administrator roles govern access. Audit logs cover authentication, queries, exports, and data or permission changes. Background jobs support leader election, restarts, interrupted-upload recovery, and collector health checks. Database backups include validation, retention, an optional off-site hook, and a restore-drill script.

<details>
<summary>Architecture</summary>

![OmniPanel architecture](docs/images/architecture.png)

The React console calls FastAPI through a same-origin proxy. The API handles ingestion, analytics, authentication, and scheduled work against PostgreSQL. SQL queries use a separate restricted login. Optional integrations add collection, AI queries, and notifications.

See the [architecture guide](docs/architecture.md) for the data model and service boundaries.

</details>

## Documentation

| Start here | Extend and operate |
|---|---|
| [Installation and login](docs/getting-started.md) | [Architecture](docs/architecture.md) |
| [Frontend development and preview](frontend/README.md) | [Collector setup](docs/collector.md) |
| [WorkBuddy integration](docs/workbuddy-integration.md) | [WeChat synchronization](docs/wechat-auto-sync.md) |
| [Natural-language queries](docs/nl-to-sql.md) | [Maintenance and secret rotation](docs/maintenance.md) |
| [Testing with synthetic data](docs/testing.md) | [Upgrading](docs/upgrading.md) |

## Contribute

Contributions to connectors, metric definitions, tests, and the interface are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), or open an [issue](https://github.com/Nanboy-Ronan/OmniPanel/issues) with a reproducible example. Use synthetic fixtures and screenshots; keep credentials, real exports, and organization-specific identifiers out of contributions.

Report security issues through [SECURITY.md](SECURITY.md).

## License

[GNU Affero General Public License v3.0](LICENSE).

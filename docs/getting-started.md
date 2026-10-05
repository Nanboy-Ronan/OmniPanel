# Getting Started

[English](getting-started.md) | [中文](getting-started.zh-CN.md)

This guide covers installing OmniPanel, configuring it, running both
services, and creating the first user.

## Requirements

- Python 3.13+
- Node.js 22.18+ and npm
- A PostgreSQL server (13+) reachable from where the backend runs
- (Optional) Redis — only used for distributed rate limiting; the app falls
  back to in-process limiting if Redis is unreachable

## 1. Install dependencies

```bash
git clone <your-fork-url> omnipanel
cd omnipanel
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure environment

```bash
cp .env.example .env
```

At minimum, edit `.env` and set:

```dotenv
RAP_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>:5432/<dbname>
RAP_SECRET=<a long random string>
CORS_ORIGINS=http://localhost:5173
WECOM_CONSOLE_REDIRECT_URI=http://localhost:5173/console/
WECOM_CORP_ID=<your corporation ID>
WECOM_AGENT_ID=<your application ID>
WECOM_APP_SECRET=<your application secret>
```

`RAP_SECRET` signs authentication tokens — generate one with, e.g.:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

See [Configuration](architecture.md#configuration-reference) for the full
list of environment variables, or just read `.env.example`, which documents
every optional one inline.

## 3. Create the database

Create an empty PostgreSQL database matching `RAP_DATABASE_URL`:

```bash
createdb <dbname>
```

## 4. Apply migrations

```bash
make db-upgrade        # equivalent to: alembic upgrade head
```

This creates every table the app needs. Verify with:

```bash
make db-check
```

## 5. Run the backend

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

On startup the app:
- registers all ORM models with SQLAlchemy
- tries to acquire a "leader" lock (see `app/utils/leader.py`) — only the
  leader process runs the background backup/sync loops, so it's safe to run
  multiple backend workers
- exposes `GET /health` (checks DB + Redis) and `GET /ping`

## 6. Run the frontend

In a separate shell:

```bash
npm --prefix frontend ci
make ui
```

Open `http://localhost:5173/console/`. Vite proxies `/api/` to FastAPI. Register your public callback domain with Enterprise WeChat before deploying.

## 7. Create the first user

Sign in with Enterprise WeChat. **The first authenticated user becomes an
administrator**; later users receive `WECOM_DEFAULT_ROLE` (viewer by default).
Configure WeCom credentials and the callback before trying the real login flow.
For a credential-free UI tour, build the frontend and run
`node frontend/scripts/preview-fixtures.mjs`, then open
`http://127.0.0.1:5180/console/`. This local preview only serves synthetic data.

## 8. Upload your first export

From your business or content platform's back-office, export an order or
article report (the formats OmniPanel currently auto-detects are documented
in [Architecture → Data ingestion](architecture.md#data-ingestion-etl)). Upload
it from the UI's Upload screen, then check Analysis for the resulting
metrics.

## Running with HTTPS directly (optional)

For local/standalone HTTPS without a reverse proxy, set:

```dotenv
SSL_KEYFILE=/path/to/key.pem
SSL_CERTFILE=/path/to/cert.pem
```

`app/main.py` picks these up automatically when run as
`python -m app.main`. In production, terminating TLS at a reverse proxy
(nginx, Caddy, etc.) in front of plain HTTP uvicorn is the more common setup
— in that case leave these unset and make sure `PROXY_HEADERS=true` (the
default) so client IPs are read from `X-Forwarded-For`.

## Troubleshooting

**`make db-upgrade` fails with "connection refused" or "role does not exist"**

Check that `RAP_DATABASE_URL` in `.env` points at a running PostgreSQL instance and that the user/database in the URL exist. Create the database first with `createdb <dbname>` if you haven't already.

**Backend starts but `GET /health` returns unhealthy**

The health endpoint checks both the database connection and (if `REDIS_URL` is set) Redis. If Redis is unreachable, the app falls back gracefully and `/health` will still report it — but no other functionality is blocked. If the database check fails, verify `RAP_DATABASE_URL` and that migrations have been applied.

**React shows "Connection refused" when loading data**

The browser calls same-origin `/api/`. During development Vite proxies it to `http://127.0.0.1:8000` (`API_PROXY_TARGET` overrides this); Compose uses Nginx. Check that the API is running and the proxy target is reachable.

**File upload is rejected with "unrecognized column set"**

OmniPanel identifies the platform purely by column names. Make sure you're uploading the raw export file from the platform's back-office — not a file you've reformatted. The recognized column fingerprints are documented in [Architecture → Data ingestion](architecture.md#data-ingestion-etl).

**The first registered user is not admin**

The auto-promotion to `admin` only applies to the very first user row inserted into the `user` table. If you dropped and re-created the database and registered again, it should work. If the table already had rows from a prior setup, promote manually:

```sql
UPDATE "user" SET role = 'admin' WHERE email = 'your@email.com';
```

**NL-to-SQL returns 503**

No LLM provider API key is configured. Set at least one key in `.env` (e.g. `MINIMAX_API_KEY=...`) and restart the backend.

## Next steps

- [Architecture](architecture.md) — how the pieces fit together, the data
  model, and the full configuration reference
- [中文问数据 (NL-to-SQL)](nl-to-sql.md) — enable natural-language querying
- [Testing](testing.md) — run the test suite against your own PostgreSQL

# WorkBuddy integration

OmniPanel supports scoped read-only MCP queries, per-user OAuth authorization,
queued weekly report generation, and owner-approved collector login repair.
All integration examples are synthetic. No tenant IDs, credentials, cookies,
production exports, or private deployment history are distributed.

## Enable read-only queries

1. Back up the database and run `python -m alembic upgrade head` (0023–0029).
2. Set `PUBLIC_BASE_URL` to your public HTTPS origin (legacy `/console/` URLs
   also work), and explicitly enable `WORKBUDDY_MCP_ENABLED=true`.
3. Proxy `/api/` and the root `/.well-known/oauth-*` routes to FastAPI.
   The included Nginx configuration supports both discovery and streaming MCP.
4. Configure your WorkBuddy connector for
   `https://dashboard.example.com/api/integrations/workbuddy/mcp`, replacing
   the example origin with your deployment. For the official web client, set
   `WORKBUDDY_WEB_CALLBACK_URL` to the exact HTTPS callback assigned to your
   connector. It is disabled by default; arbitrary remote callbacks are rejected.

Administrators and analysts authorize scopes in the console after WeCom sign-in.
Viewers cannot connect. Each request checks the current user and granted scopes.
OAuth uses PKCE, one-time codes and rotating refresh tokens; revoked or disabled
users lose access. Personal tokens remain available for compatible clients.
Queries expose bounded analytics and media detail, never customer identities,
secrets, collector sessions, or arbitrary SQL. Audit records contain scope,
filters, result counts, outcome and duration, without result bodies.
Tool calls are rate limited per credential (a burst budget that stops runaway
loops), and the client IP and user agent are recorded with each audit row.
Disabling a user or downgrading them to viewer revokes all of their WorkBuddy
connections; administrators can also revoke a single connection from the console.

## Optional weekly report provider

The default `REPORT_PROVIDER=legacy` keeps existing report generation.
To opt in, set `REPORT_PROVIDER=workbuddy`, `WORKBUDDY_API_KEY`,
`WORKBUDDY_SOURCE_APP=OmniPanel`, and your own `WORKBUDDY_AGENT_ID`.
Store credentials only in the server environment. Timeout is controlled by
`WORKBUDDY_REPORT_TIMEOUT_SECONDS` (30–600 seconds).

This opt-in sends an aggregate statistics snapshot to the configured WorkBuddy
service. It contains metric definitions, completeness warnings and evidence IDs,
not customer identity fields or credentials. The worker validates returned metric
references and renders the result; queue status and retry controls appear in the
weekly reports page. Delivery uses existing per-user WeCom notification settings.
No external WorkBuddy or WeCom requests are required for the synthetic tests.

Interrupted jobs recover without duplicate sends: a stale `preparing` job is
retried at most three times, then marked failed; a job interrupted while sending
becomes `delivery_unknown` and is never resent automatically. Administrators can
retry, confirm receipt, or regenerate a failed week from the reports page.
Generation failures, missing narratives, failed or unconfirmed deliveries send a
single `[周报告警]` to the users selected for WeCom alerts. Reports that the
legacy provider generated but did not deliver are handed to the WorkBuddy
worker unchanged. `WECOM_ALERT_TOUSER` can only narrow recipients: if any listed
WeCom ID is not an active analyst or administrator with a linked account, the
report is not queued and the error names those IDs.

## Local collector login repair

Administrators assign owners for XHS, Pugongying and Channels accounts. A repair
requires a fresh, short-lived pairing approval and local browser login. Only
approved portal cookies are uploaded to the configured HTTPS deployment, then
verified against the expected account before an atomic replacement. Revocation
and ownership are checked again before installation. Failure preserves the old
session. Pairing secrets and cookies are never printed or stored in chat.

The local helper prints a one-time confirmation code (for example `ABCD-EFGH`).
The owner must enter it on the console approval page; five wrong codes lock the
request. This stops someone from starting a repair and forwarding the link to an
owner for approval. The helper only opens console links on its own origin.

The re-check runs headless inside the backend process, so the backend needs
`PLAYWRIGHT_BROWSERS_PATH` and Chromium just like the collector. Administrators
can see the re-check environment on the WorkBuddy page
(`GET /api/collector/repair/readiness`); failures are logged as
`collector repair verification error`. A repair waits up to 90 seconds for a
running collect of the same account before returning `collector_busy`. Every
outcome (success, verification failure, busy, `revoked`) is audited.

When an account's internal name differs from what the portal shows (a suffix
such as 「（主号）」, or a brand name on Pugongying), set its portal display name
in the account editor; the re-check matches that name instead.

After a successful repair the platform queues one immediate collect for that
account (see `run-requests` in [collector.md](collector.md)), so new data arrives
within minutes instead of at the next scheduled run.

Build deployment packages from source; no company-built archives are included:

```sh
python integrations/collector-login/build_connector.py --origin https://dashboard.example.com
```

Replace the example origin with your deployment. This builds a wheel, connector
ZIP, enterprise skill ZIP and `release.json` under the ignored
`integrations/collector-login/dist/` directory, with the same version and SHA-256
pinned everywhere. The helper download route only serves the files named by
`release.json`; until you build, nothing is downloadable.
`tests/test_collector_login_release.py` builds into a temporary directory and
fails if any pin drifts. Bump the version in `pyproject.toml`,
`rpa_collector_login/__init__.py` and `connector/connector-meta.json` for any
helper change, since installed clients will not reinstall the same version.
Helper 1.0.1 adds the confirmation code; 1.0.0 helpers cannot complete a repair. The build only uses a temporary source copy, leaving public templates
unchanged. Python with pip, setuptools and wheel is required.

Distribute the generated connector or skill through your own WorkBuddy workspace.
The helper uses an isolated browser; it does not read the user's browser profile.
`OMNIPANEL_ORIGIN` can override the packaged origin for a trusted deployment.
Do not distribute a package pointing to the example domain.

The Windows script is UTF-8 with BOM and CRLF line endings, downloads a pinned
uv release archive verified by SHA-256 (it never runs a downloaded script), and
judges native commands by exit code. Where policy blocks unsigned scripts,
deploy a signed copy instead of bypassing the execution policy.

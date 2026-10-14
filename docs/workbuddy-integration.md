# WorkBuddy integration

OmniPanel supports scoped read-only MCP queries, per-user OAuth authorization,
queued weekly report generation, and owner-approved collector login repair.
All integration examples are synthetic. No tenant IDs, credentials, cookies,
production exports, or private deployment history are distributed.

## Enable read-only queries

1. Back up the database and run `python -m alembic upgrade head` (0023–0026).
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

## Local collector login repair

Administrators assign owners for XHS, Pugongying and Channels accounts. A repair
requires a fresh, short-lived pairing approval and local browser login. Only
approved portal cookies are uploaded to the configured HTTPS deployment, then
verified against the expected account before an atomic replacement. Revocation
and ownership are checked again before installation. Failure preserves the old
session. Pairing secrets and cookies are never printed or stored in chat.

Build deployment packages from source; no company-built archives are included:

```sh
python integrations/collector-login/build_connector.py --origin https://dashboard.example.com
```

Replace the example origin with your deployment. This builds a wheel, connector
ZIP and enterprise skill ZIP under the ignored `integrations/collector-login/dist/`
directory, with matching SHA-256 hashes. Serve these through the helper download
route. The build only uses a temporary source copy, leaving public templates
unchanged. Python with pip, setuptools and wheel is required.

Distribute the generated connector or skill through your own WorkBuddy workspace.
The helper uses an isolated browser; it does not read the user's browser profile.
`OMNIPANEL_ORIGIN` can override the packaged origin for a trusted deployment.
Do not distribute a package pointing to the example domain.

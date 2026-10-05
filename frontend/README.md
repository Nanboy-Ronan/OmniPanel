# OmniPanel · React + TypeScript

The React workbench provides 22 routes for commerce BI, customers, orders,
content analytics, reporting, and administration. FastAPI owns authentication,
authorization, ingestion, analytics, and background jobs.

## Development

Requires Node.js 22.18+ and a running API (`make api` from the repository root).

```sh
npm ci
npm run dev
```

Open `http://localhost:5173/console/`. Vite proxies same-origin `/api/` requests
to `http://127.0.0.1:8000`; set `API_PROXY_TARGET` to use a different local API.

The workbench uses Enterprise WeChat login. Configure `WECOM_CORP_ID`,
`WECOM_AGENT_ID`, `WECOM_APP_SECRET`, and `WECOM_CONSOLE_REDIRECT_URI` in the
backend environment, and register the deployment's callback domain in WeCom.
The first authenticated user becomes an administrator. For production, the
callback URL should be your own `https://dashboard.example.com/console/` URL.
Legacy callback environment variables remain supported by the backend.

## Checks and synthetic preview

```sh
npm run format:check
npm test
npm run build
node scripts/preview-fixtures.mjs
```

The preview is at `http://127.0.0.1:5180/console/`. Its login and data are entirely
synthetic; it never connects to a business database. It is a local review tool,
not part of the production build. README screenshots are captured from this
preview, with no production credentials or exports. To regenerate them, install
Python Playwright and Chromium in a separate tooling environment, keep this
preview running, then run `python scripts/capture_screenshots.py`.

## Structure

- `src/lib/api.ts`: same-origin API requests, session token, cancellation, timeout,
  response validation, and error handling. Mutations are not automatically retried.
- `src/lib/auth.ts`: removes OAuth code/state from the URL before rendering and
  exchanges each callback once. Session state and query caches clear on logout/401.
- `src/lib/routes.ts`: routes, role requirements, and legacy Chinese page aliases.
- `src/components/workspace.tsx`: filters, sorting, paging, CSV, and confirmation.
- `src/components/CommerceDashboard.tsx`: linked commerce charts and drill-downs.
- `src/components/ContentExplorer.tsx`: readable content distributions and analysis.
- `src/components/PgyAnalytics.tsx`: cooperation cohorts and blogger performance.
- `src/lib/report.ts`: renders archived charts without executing embedded scripts.

## Deployment

The root Compose file builds `frontend/Dockerfile` and serves the production
bundle through Nginx at `/console/`, with `/api/` proxied to the backend.
`frontend/nginx.conf` is the self-hosted proxy configuration. Set the public
callback and `PUBLIC_BASE_URL` for your domain, and configure TLS at your ingress.
Only the frontend bundle is served publicly; test fixtures stay out of `dist`.

When deploying static files without Compose, serve `dist` at `/console/`, fall
back to `index.html` for page navigation, and proxy `/api/` to FastAPI. Retain old
hashed assets during rolling releases so existing tabs can load their chunks.
CI runs backend tests plus frontend formatting, tests, and the production build.

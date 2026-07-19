# Creator-Portal Export Agent

[English](collector.md) | [中文](collector.zh-CN.md)

## Why this exists

Xiaohongshu (小红书) and Zhihu (知乎) have no public analytics API. Getting
account-level content metrics (impressions, click-through, watch time, …)
requires a human to log into the creator portal and click "export". This
subsystem automates that: a saved browser login state is reused to open the
portal, trigger the export, and upload the downloaded file through the
**existing** `/media/xhs/upload` / `/media/zhihu/upload` endpoints — the ETL,
dedup, and audit logging are unchanged from a manual upload.

It does **not** replace manual uploads; it's an unattended way to run the same
click-download-upload sequence a human already does.

## Architecture

- `app/collector/` — a standalone package. Uses [Playwright](https://playwright.dev/python/)
  (sync API). **Nothing outside `app/collector/` imports Playwright** — the
  FastAPI backend and Streamlit UI never load a browser.
- Runs as a separate process (`python -m app.collector collect`), independent
  of the web app process — trigger it however fits your deployment (cron,
  systemd timer, a scheduled CI job, manually).
- Uploads go through the API using a dedicated service-account user (via
  `app/ui/api_client.py`'s `APIClient`) — identical code path to a human
  clicking "upload" in the UI.
- Run status is written directly to Postgres (`CollectorRun` /
  `collector_runs`, same pattern as `MediaSyncRun` for WeChat auto-sync).
- Optional WeCom alert (`app/utils/wecom_bot.py`) fires on session expiry,
  download timeout, or upload failure — sent via a WeCom self-built app
  message (`cgi-bin/message/send`), not a group-bot webhook. See "Alerting"
  below for why.

## First-time setup: bootstrap a login (local machine)

Portal login state can't be created headlessly — it requires a human to
complete the live login. Do this on your own machine, not the server:

```bash
pip install -r requirements.txt
playwright install chromium

python -m app.collector bootstrap-login --platform xhs --out xhs_session.json
# A Chromium window opens on the XHS login page (pro.xiaohongshu.com/login).
# Log in with phone number + SMS code — XHS creator accounts use SMS login,
# not a QR scan. If your phone number is linked to more than one XHS
# professional account, you'll land on an account-picker page — click the
# account you want this session file to represent. The script waits through
# that step automatically and writes xhs_session.json once a real dashboard
# loads (8 min timeout total).

python -m app.collector bootstrap-login --platform zhihu --out zhihu_session.json
```

Then upload the resulting JSON file via the Streamlit admin page ("自动采集",
only visible to admin users): select the platform (and, for XHS, the account
it belongs to — one session file per account if the phone number has
several) and upload. It's written server-side to
`{COLLECTOR_DIR}/sessions/xhs_{account_id}.json` or `.../zhihu.json`, mode
`0600`.

**Session lifetime**: creator-portal sessions typically last a few weeks.
Every successful collector run re-saves the (rotated) cookies back to the
same file, which extends this in practice. When a session finally expires,
the collector detects it (repeated redirects to the login flow that don't
resolve — see "XHS auth is CAS-based" below for why a *transient* redirect is
normal and not treated as expiry), records `status=session_expired` on that
`CollectorRun`, and — if WeCom alerting is configured — posts an alert naming
the platform/account. Fix: repeat `bootstrap-login` for that platform and
re-upload.

## XHS auth is CAS-based — read this before touching `xhs.py`

The short version, because it's easy to "fix" this into a worse state (full
narrative in the `app/collector/xhs.py` / `browser.py` module docstrings):

- Login is at `pro.xiaohongshu.com/login`. The actual note-level data lives
  on a **different subdomain**, `creator.xiaohongshu.com`.
- Reaching `creator.xiaohongshu.com` authenticated requires a **CAS
  service-ticket handoff**, not plain shared-cookie SSO. A fresh navigation
  there transiently 401s and shows a login-looking URL for a few seconds
  *while the ticket exchange runs in the background*, then the page silently
  redirects itself to the real content — typically ~6s. `collect_xhs()`'s
  login check (`_goto_and_check_login`) deliberately waits up to 15s and only
  declares `SessionExpiredError` if the login state is *still* there at the
  end — don't shorten that budget or make the check return on first sight of
  a login URL.
- `open_context()` in `browser.py` does **not** pass
  `--disable-blink-features=AutomationControlled` to Chromium. That flag is a
  standard anti-detection trick, but it backfired: XHS's risk control flagged
  its *presence* (no real user browser has it) and force-expired an otherwise
  valid session. Don't add it back without re-testing live.
- `COLLECTOR_HEADLESS=true` (real headless, no visible window) has **not**
  been verified against XHS — only `headless=False` has been confirmed
  end-to-end. Until someone verifies headless separately, default to
  `COLLECTOR_HEADLESS=false` and run under a virtual display (e.g. `xvfb-run`)
  on a display-less server.

## Configuration

All settings are environment variables (also readable from `.env`):

| Variable | Default | Description |
|---|---|---|
| `COLLECTOR_ENABLED` | `false` | Master kill-switch; `collect` exits 0 immediately when false |
| `COLLECTOR_XHS_ENABLED` | `true` | Include XHS accounts in a run |
| `COLLECTOR_ZHIHU_ENABLED` | `true` | Include Zhihu (article+qa) in a run |
| `COLLECTOR_DIR` | `data/collector` | Sessions/downloads/debug root |
| `COLLECTOR_HEADLESS` | `false` | Only `false` (+ a virtual display on a headless server) is verified against XHS; true headless is untested — see above |
| `COLLECTOR_API_URL` | `http://127.0.0.1:8000` | Where the collector uploads to |
| `COLLECTOR_SERVICE_EMAIL` / `COLLECTOR_SERVICE_PASSWORD` | — | Service-account credentials (viewer role) |
| `COLLECTOR_NAV_TIMEOUT_SECONDS` | `45` | Playwright navigation timeout |
| `COLLECTOR_DOWNLOAD_TIMEOUT_SECONDS` | `120` | How long to wait for the export file |
| `COLLECTOR_DEBUG_KEEP` | `20` | Max failure screenshot+HTML pairs retained |
| `COLLECTOR_COLLECT_RETRIES` | `1` | Retries for a transient download timeout before failing the run |
| `WECOM_ALERT_TOUSER` | `@all` (optional) | Who receives notifications — see "Alerting" below |
| `WECOM_NOTIFY_SUCCESS` | `true` | Also send a WeCom notification when a run completes with no failures — set `false` for failure-only alerting |

Alerting also needs `WECOM_CORP_ID` / `WECOM_AGENT_ID` / `WECOM_APP_SECRET`
(shared with the Enterprise WeChat OAuth login — not collector-specific
variables).

## Alerting

Every run sends exactly one WeCom notification — success or failure, never
both — so a completed run is never silent either way:

- **All targets succeeded**: a summary message with per-target row counts,
  unless `WECOM_NOTIFY_SUCCESS=false`. Skipped entirely for `--dry-run`
  (local debugging noise, not a real run).
- **Any target failed** (session expired, download timeout, upload rejected,
  or any other error): an alert listing each failure, followed by a section
  for any targets that *did* succeed in the same run — so a partial failure
  isn't silent about the rest.

Sent via a WeCom self-built app (企业微信自建应用) using the app-message API
(`cgi-bin/message/send`), reusing `WECOM_CORP_ID` / `WECOM_AGENT_ID` /
`WECOM_APP_SECRET` — the same credentials already configured for Enterprise
WeChat OAuth login (`app/views/wecom_auth.py`). No separate secret to manage;
if any of those three are unset, notifications are silently skipped and
nothing else is affected.

**Why not a group-bot webhook** (企业微信自定义群机器人), the more common
approach: some WeCom (Enterprise WeChat) organizations have custom
group-robot creation disabled with no self-serve way to re-enable it.
App-message sending sidesteps that permission entirely — any self-built app
can message its visible users without needing group-robot rights.

Recipient defaults to `WECOM_ALERT_TOUSER=@all` (every user visible to the
app). To target specific people instead, use their WeCom userid (visible via
`SELECT wecom_userid FROM "user" WHERE wecom_userid IS NOT NULL` for anyone
who has logged in via WeCom OAuth at least once), `|`-separated for multiple,
e.g. `WECOM_ALERT_TOUSER=userid1|userid2`.

## CLI

```bash
# Local, one-time, per platform:
python -m app.collector bootstrap-login --platform xhs --out xhs_1.json
python -m app.collector bootstrap-login --platform zhihu --out zhihu.json

# Manual run (server or local against a local backend):
python -m app.collector collect                                # all enabled targets
python -m app.collector collect --platform xhs --account-id 3
python -m app.collector collect --platform zhihu --content-type article
python -m app.collector collect --dry-run                       # download only, skip upload
python -m app.collector collect --headed                        # force a visible window (default is already headed; see COLLECTOR_HEADLESS above)

# Check whether a saved session is still logged in, without downloading:
python -m app.collector verify-session --platform xhs --account-id 3
```

## Selector maintenance (things will break)

Both `app/collector/xhs.py` and `app/collector/zhihu.py` keep every
portal-specific URL/selector in one constants block at the top of the file.

**XHS is fully verified end-to-end** (login → export → upload → `xhs_posts`
rows, confirmed idempotent on re-run). **Zhihu's selectors remain unverified
placeholders** — expect the same kind of surprises XHS had (wrong domain,
wrong login mechanism, transient auth-redirect timing) and budget real
debugging time against a live account, not just a selector tweak.

When the portal changes its UI and a run starts failing with
`download_failed`:

1. Check `{COLLECTOR_DIR}/debug/` for the screenshot+HTML pair from the
   failed run (named `{timestamp}_{tag}.png`/`.html`).
2. Update the constant(s) in the relevant module — nothing else should need
   to change.
3. Redeploy.

Prefer text/role locators (`button:has-text("导出数据")`) over CSS classes —
XHS/Zhihu use hashed/generated class names that change on every frontend
build.

## Monitoring

```sql
SELECT platform, account_id, content_type, status, rows_upserted,
       error_message, started_at, finished_at
FROM collector_runs
ORDER BY started_at DESC
LIMIT 20;
```

Also visible in the Streamlit "自动采集" admin page (session status + recent
runs table).

## Pipeline health watchdog

Per-run notifications (this collector's, and the WeChat auto-sync's — see
[wechat-auto-sync.md](wechat-auto-sync.md)) only fire when a run actually
happens. They say nothing if a pipeline stops running altogether — a
disabled scheduler, a crashed process, a misconfigured host.
`app.scheduler.watchdog_loop` is the daily backstop: it checks whether each
enabled pipeline has a recent run recorded, and alerts only when something
looks stale — a healthy day produces no message (avoiding duplicate noise
with the per-run success notifications above).

| Variable | Default | Description |
|---|---|---|
| `WATCHDOG_ENABLED` | `true` | Master switch for the daily health check |
| `WATCHDOG_HOUR` | `9` | Hour (0-23, `APP_TIMEZONE`) the check runs |
| `WATCHDOG_MAX_AGE_HOURS` | `30` | Collector / WeChat sync: alert if no run recorded in this long |
| `WATCHDOG_BACKUP_MAX_AGE_DAYS` | `35` | Monthly backup: alert if no successful backup in this long (30-day cadence + buffer) |

Checked pipelines (each skipped if its own feature is disabled):

- **Collector** (`COLLECTOR_ENABLED=true`): most recent `collector_runs.started_at`.
- **WeChat auto-sync** (`WECHAT_AUTO_SYNC_ENABLED=true`): most recent
  `media_sync_runs.started_at` where `source = 'api'` — a manual xlsx upload
  writes a fresh row too but must not mask an auto-sync that has actually
  stopped.
- **Monthly backup** (unless `RAP_DISABLE_MONTHLY_BACKUP=true`): the
  `.last_monthly_backup` stamp file `app/db/backup.py` already maintains.

## What stays manual forever

- Real portal login (SMS code, and clicking through the account picker if the
  phone number has multiple accounts).
- Anti-bot / risk-control behavior on the live portal (what triggers it,
  whether it changes over time).
- Selector validity — no automated check catches a silent portal UI change
  short of a `download_failed`/empty-export run.

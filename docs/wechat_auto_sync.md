# WeChat Auto-Sync

## Why this exists

WeChat's DataCube API (`getarticletotaldetail`) retains per-article engagement
statistics for approximately **180 days** from each article's publish date.
Once an article ages past that window, WeChat purges its metrics — they cannot
be recovered via the API (errcode 61501 is returned for any date query on
expired articles).

Without automated syncing, any article published more than 6 months ago before
you first ran a manual sync will have no retrievable stats.  The auto-sync
solves this by running daily and pulling every article's metrics before they
expire.

## How it works

1. At the configured hour each day, a background asyncio task wakes up.
2. It computes the sync window: `[today − window_days, today − 2 days]`.
   - The 2-day offset accounts for WeChat's 1–2 day processing lag.
   - The default 170-day window leaves a 10-day buffer before the 180-day expiry.
3. It calls `getarticletotaldetail` for each day in that range across all
   configured WeChat accounts.
4. Results are upserted into `media_posts` and `media_post_metrics_daily`.
   Re-running is safe — existing rows are updated with the latest values.
5. Each run is recorded in `media_sync_runs` (status, posts/metrics counts,
   timestamps, error messages).

## Configuration

All settings are environment variables (also readable from `.env`):

| Variable | Default | Description |
|---|---|---|
| `WECHAT_AUTO_SYNC_ENABLED` | `false` | Set to `true` to enable the scheduler |
| `WECHAT_AUTO_SYNC_WINDOW_DAYS` | `170` | Days of history to cover per run |
| `WECHAT_AUTO_SYNC_HOUR` | `3` | Hour of day (0–23) to run, in `APP_TIMEZONE` |
| `APP_TIMEZONE` | `Asia/Shanghai` | IANA timezone for scheduling |

### Minimal `.env` addition to enable

```dotenv
WECHAT_AUTO_SYNC_ENABLED=true
```

The scheduler uses the same `WECHAT_APP_ID_N` / `WECHAT_APP_SECRET_N`
credentials as the manual sync.  See `docs/credentials.md` for credential
setup.

## First-time setup on a new server

On a freshly deployed server with no historical data, run a one-time backfill
via the admin UI (Media → WeChat Sync) with:

- **Start date**: today − 170 days
- **End date**: today − 2 days

After that, the daily scheduler maintains coverage going forward.

## What happens if a sync run is skipped?

Missing one daily run is harmless — the next run covers the same date range.
Missing several weeks is also recoverable as long as articles are still within
the 180-day window.  The risk is only if the scheduler is disabled for more
than ~10 days (the safety buffer), at which point the oldest articles in the
window begin losing data.

## Monitoring

Check the `media_sync_runs` table for recent run history:

```sql
SELECT account_id, status, start_date, end_date,
       posts_upserted, metrics_upserted, error_message, finished_at
FROM media_sync_runs
ORDER BY finished_at DESC
LIMIT 20;
```

Failed runs appear with `status = 'failed'` and an `error_message`.  Common
causes:

| Error | Cause | Fix |
|---|---|---|
| `40001 invalid credential` | Access token expired mid-sync | Credentials rotate automatically; transient — usually resolves on next run |
| `61501` | Date is outside the retention window or data not yet available | Normal for very recent dates; the 2-day lag offset should prevent this |
| `40164 not whitelisted` | Server IP not in WeChat IP whitelist | Add the server's outbound IP to the WeChat Official Account platform |

## Architecture notes

The scheduler lives in `app/scheduler.py` and is started as an `asyncio` task
in the FastAPI app lifespan (`app/main.py`).  It imports the sync logic
directly from `app/views/media/routes.py` (`_sync_one_wechat_account`,
`_ensure_env_wechat_accounts`) rather than going through HTTP, so no
self-calling is needed.

The task is cancelled cleanly when the application shuts down (SIGTERM /
uvicorn reload) because `asyncio.CancelledError` is propagated rather than
swallowed.

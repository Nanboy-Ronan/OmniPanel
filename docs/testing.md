# Testing

[English](testing.md) | [中文](testing.zh-CN.md)

## Running the suite

The tests need a reachable PostgreSQL server. They never touch your real
application database — `tests/conftest.py` creates a uniquely-named
`rpa_test_*` database for the session, runs every test against it, and
drops it at teardown.

```bash
export PG_TEST_URL=postgresql://user:pass@127.0.0.1:5432/postgres
make test          # equivalent to: pytest tests/ -q
```

`PG_TEST_URL` only needs to point at a database server you have credentials
for (it connects to `postgres` as an admin database to issue `CREATE
DATABASE`/`DROP DATABASE`) — it is never used as the database tests
actually run against. If `PG_TEST_URL` isn't set, `RAP_DATABASE_URL` is used
for the same purpose, and finally a `postgresql://rpa:rpa@127.0.0.1:5432/rpa`
fallback.

Safety rails baked into `conftest.py`:
- `_db_url()` refuses to build a connection string for any database name
  that doesn't start with `rpa_test_` (or isn't the admin `postgres` db).
- The teardown's `DROP DATABASE` call re-checks the `rpa_test_` prefix
  before dropping anything.
- Between tests, an autouse fixture truncates every data table — but only
  after confirming the active database is an `rpa_test_*` one.

This means pointing `PG_TEST_URL` at a shared/staging Postgres server is
safe: the worst that can happen is a throwaway `rpa_test_<uuid>` database
being created and dropped.

## What the suite covers

- ETL: platform detection, normalization, de-duplication on re-ingest
  (`test_full_row_uniqueness.py`, `test_multiplatform.py`,
  `test_platform_raw_tables.py`)
- API endpoints: auth, uploads, analytics, SQL console, admin
  (`test_api_endpoints.py`, `test_upload_validation.py`,
  `test_upload_background.py`, `test_sql_console.py`)
- Self-media ingestion: WeChat, 小红书, 知乎 (`test_xhs.py`,
  `test_xhs_accounts.py`, `test_zhihu.py`)
- Caching, rate limiting, structured logging, content-impact analytics

## The synthetic dataset

Several e-commerce tests assert against exact aggregate numbers (order
counts, revenue totals, top SKU). Rather than depending on a real exported
order file, those tests use a small, fully fabricated dataset in
`tests/sample_dataset.py`:

```python
from sample_dataset import synthetic_youzan_df

df = synthetic_youzan_df()   # pandas DataFrame, raw 有赞 (Youzan) column headers
```

It has known, documented properties tests assert against directly —
e.g. `SAMPLE_CUSTOMER_PHONE`, `SAMPLE_CUSTOMER_JULY_ORDER_COUNT`,
`SAMPLE_CUSTOMER_JULY_TOTAL` are exported alongside the dataset so
assertions don't need magic numbers scattered through test files. If you
add a test that needs different aggregate shapes, prefer extending this
module over inlining another ad-hoc CSV string.

## Tests that are skipped by default

`tests/test_media_upload.py` covers a WeChat xlsx-upload code path that's
disabled in this codebase (WeChat data arrives via the official API sync
instead — see [WeChat auto-sync](wechat-auto-sync.md)); the whole module is
marked `pytest.mark.skip`. Remove the module-level `pytestmark` line to
re-enable it if you bring that upload path back into use.

There is intentionally no test fixture that depends on a real customer
export file shipped in the repo — `data/` is gitignored, and every test
that needs order-shaped input uses the synthetic dataset above instead.

## Fast password hashing in tests

`conftest.py` swaps in a trivial password helper
(`RAP_TEST_FAST_PASSWORDS=true`, set by default for the test session) so
tests don't pay real bcrypt cost on every login. This only takes effect
when that env var is set — it never affects a normal run of the app.

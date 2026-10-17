# Dependency Maintenance

[English](maintenance.md) | [中文](maintenance.zh-CN.md)

`requirements.txt` pins **exact versions** for every package. This is deliberate: in a
self-hosted deployment, a reproducible environment matters more than "always latest."
The tradeoff is that dependencies never update themselves — they need a deliberate,
recurring upgrade cadence, or the project slowly accumulates known CVEs and an
increasingly painful version gap.

## Cadence

- **Quarterly** routine upgrades (January, April, July, October).
- **Security advisories are handled immediately**, out of cadence, when a GitHub
  Dependabot alert or CVE notice touches one of the critical packages below.

## Critical packages (upgrade first)

Security-sensitive — any known vulnerability here should be tracked as soon as possible:

| Package | Role | Risk surface |
|---|---|---|
| `cryptography` | JWT / TLS primitives | Cryptographic vulnerabilities |
| `fastapi-users` / `fastapi-users-db-sqlalchemy` | Auth framework | Auth bypass |
| `fastapi` / `starlette` | Web framework | Request handling, DoS |
| `uvicorn` / `h11` / `httptools` | HTTP server/parsing | Request smuggling, DoS |
| `argon2-cffi` / `bcrypt` | Password hashing | Hash strength |
| `pydantic` / `pydantic-settings` | Input validation | Validation bypass |
| `asyncpg` / `psycopg2-binary` / `sqlalchemy` | DB driver/ORM | Injection, connection handling |
| `anthropic` / `openai` | NL-to-SQL provider SDKs | Dependency chain |

Everything else (pandas, SQLAlchemy, etc.) can follow the quarterly cadence.

## Upgrade process

```bash
# 1. Branch
git checkout -b chore/deps-YYYYQN

# 2. See which pinned versions are outdated
make deps-outdated

# 3. Scan for known CVEs (doesn't touch the runtime env — runs via pipx)
make deps-audit

# 4. Bump versions in requirements.txt (critical packages first; don't jump
#    too many major versions at once). Then, in the project venv:
#    pip install -r requirements.txt

# 5. Tests are the only gate — the full suite must pass
make test

# 6. Smoke test: start the app, run one upload + one analysis page + login
#    (see docs/getting-started.md)

# 7. Commit once green — one commit explaining which packages moved and why
```

**Principle**: a fully green test suite is a hard requirement to merge. Don't jump
multiple major versions across several packages in one go — it makes failures hard to
bisect. Critical security packages can get their own commit; the rest can batch by
ecosystem.

## Tooling notes

- `make deps-outdated` — lists packages in `requirements.txt` that have newer releases.
- `make deps-audit` — scans pinned versions for known CVEs via `pipx run pip-audit`.
  Requires [`pipx`](https://pipx.pypa.io/) installed locally; it runs in a throwaway
  environment so it never pollutes the project or base env.
  Without pipx: `python -m pip install pip-audit && python -m pip_audit -r requirements.txt`
  (do this inside the project venv, not a base/conda environment).

## Authentication secret rotation

Generate a new random `RAP_SECRET`, move the previous value into
`RAP_SECRET_PREVIOUS` (comma-separated), and restart the API. New tokens use
the new secret; existing tokens remain valid during the transition. After at
least `TOKEN_LIFETIME_SECONDS` (24 hours by default), remove the previous secret
and restart again. If a secret is compromised, remove it immediately rather
than retaining it for a transition period. Keep all values in private server
configuration, never in the repository.

## Media metric history and raw file archive

### Post metric snapshots

`xhs_posts`, `zhihu_posts`, `wx_channels_posts` and `pgy_notes` keep only the
**latest** cumulative metrics per post; each upload overwrites them. Every import
also appends to the matching `*_post_snapshots` / `pgy_note_snapshots` table: one
row per post per collection day (in `APP_TIMEZONE`; a repeat upload on the same
day replaces that day's row).

- Snapshots store the value **as it was in that day's file**: an empty metric is
  NULL in the snapshot, while the post table keeps its previous value.
- Growth over a period is the difference between two days' snapshots.
- Migration 0029 seeds one snapshot per existing post from its current values
  (dated at its last update); earlier history cannot be recovered.
- The SQL console can query these tables through the `reporting` views.

### Raw file archive

Every **successfully** imported file (orders, 小红书 notes and 数据概览, 知乎,
视频号, 蒲公英; manual uploads and collector uploads alike) is moved to the archive
directory and recorded in `raw_upload_files` (source, account, file name, sha256,
size, uploader; orders also link `upload_batch_id`). Snapshots point to it via
`source_file_id`, so any number can be traced back to its source file. A failed
import leaves no file. An unwritable archive never blocks an import; the row is
kept without `archive_path`.

| Setting | Default | Notes |
|---|---|---|
| `RAW_ARCHIVE_DIR` | `upload_archive/` under `BACKUP_DIR` | Next to the upload spool; must be writable by the backend |
| `RAW_ARCHIVE_RETENTION_DAYS` | 730 | An hourly cleanup deletes expired files and keeps the `raw_upload_files` row (`archive_path` cleared, `purged_at` set) |

The archive is not part of the database dump. Include it in your off-site backup
if you need it.

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

Everything else (altair, streamlit, pandas, etc.) can follow the quarterly cadence.

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

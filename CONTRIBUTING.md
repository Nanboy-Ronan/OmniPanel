# Contributing to OmniPanel

Thanks for considering a contribution. This project welcomes issues and pull requests of any size — typo fixes, bug reports, new platform connectors, and larger feature work are all equally welcome.

## Table of Contents

- [Ways to contribute](#ways-to-contribute)
- [Development environment](#development-environment)
- [Running tests](#running-tests)
- [Code style](#code-style)
- [Commit messages](#commit-messages)
- [Before opening a PR](#before-opening-a-pr)
- [Good areas to work on](#good-areas-to-work-on)
- [Secret scanning](#secret-scanning)
- [Code of conduct](#code-of-conduct)

## Ways to contribute

You don't have to write code to help:

- **Report a bug** — [open an issue](https://github.com/Nanboy-Ronan/OmniPanel/issues/new?template=bug_report.md) with steps to reproduce
- **Request a feature** — [open an issue](https://github.com/Nanboy-Ronan/OmniPanel/issues/new?template=feature_request.md) or start a [Discussion](https://github.com/Nanboy-Ronan/OmniPanel/discussions)
- **Improve the docs** — fix a typo, clarify a confusing step, or add missing detail
- **Add a platform connector** — new e-commerce or self-media platform support
- **Add an NL-to-SQL provider** — often a two-line change (see below)
- **Write tests** — increase coverage for an untested code path

## Development environment

```bash
# 1. Fork and clone the repo
git clone https://github.com/<your-fork>/OmniPanel.git
cd OmniPanel

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install all dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env: set RAP_DATABASE_URL to a local PostgreSQL instance
# and RAP_SECRET to any non-empty string for local development
```

Then follow [docs/getting-started.md](docs/getting-started.md) to run the backend and frontend locally.

### Generating a secret for local development

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Running tests

The test suite creates and drops a temporary PostgreSQL database — it never touches your application database. Point it at any accessible server:

```bash
export PG_TEST_URL=postgresql://user:pass@127.0.0.1:5432/postgres
make test          # equivalent to: pytest tests/ -q
```

`PG_TEST_URL` only needs credentials for a server you control. The suite creates a uniquely named `rpa_test_*` database per session and drops it at teardown. See [docs/testing.md](docs/testing.md) for what the suite covers and which tests are skipped by default.

**When adding tests:** prefer extending `tests/sample_dataset.py` over hardcoding new ad-hoc fixtures when a test needs order-shaped data. The module exports named constants (`SAMPLE_CUSTOMER_PHONE`, `SAMPLE_CUSTOMER_JULY_ORDER_COUNT`, etc.) so assertions don't need magic numbers.

## Code style

There is no auto-formatter enforced yet. Follow the patterns in the file you're editing:

- `snake_case` for all identifiers
- Type annotations on function signatures
- No unnecessary comments — the code should be self-explanatory; only add a comment when the *why* is non-obvious
- Keep imports organized: stdlib → third-party → local, one blank line between groups

When in doubt, match the style of the file you're modifying.

## Commit messages

Use short, imperative subject lines (no period at the end):

```
add JD order de-duplication test
fix phone-masking edge case for 1******xxxx format
update architecture doc: add Redis flow diagram
```

If the change needs more context, add a blank line and a paragraph body after the subject. Reference the relevant issue number (`fixes #42`, `closes #42`) in the body when one exists.

Avoid:
- `WIP`, `fix stuff`, `update code` — be specific about what changed
- Long subject lines — keep them under 72 characters

## Before opening a PR

- [ ] Run `make test` and confirm it passes
- [ ] Add or update tests for any behavior change (or explain in the PR why no test is needed)
- [ ] Keep the PR focused — one logical change per PR is easier to review than a bundle of unrelated fixes
- [ ] Reference the relevant issue number in the PR description if one exists
- [ ] Update the relevant doc page if your change affects behavior described there

## Good areas to work on

### Adding a new e-commerce platform connector

The detect → normalize → load pipeline in `app/db/etl/` is designed to be extended:

1. **Detect** (`app/db/etl/detect.py`): Add a column-fingerprint entry to `detect_platform()`. Detection is purely column-name based — no filename or content sniffing.
2. **Normalize** (`app/db/etl/normalize.py`): Add column mappings in `normalize_dataframe()` to map the platform's raw headers onto the unified schema (order id, date, customer key, SKU, quantity, price, receiver, phone, province, address, etc.).
3. **Raw table** (optional): Create an Alembic migration for a `<platform>_orders` raw table if you want to preserve the platform-native row alongside the normalized one for traceability. See the existing `youzan_orders` / `jd_orders` / `tmall_orders` tables as reference.
4. **Tests**: Add a few rows to `tests/sample_dataset.py` representing the new platform's raw column format, and a test in `tests/test_multiplatform.py`.

See [docs/architecture.md#data-ingestion-etl](docs/architecture.md#data-ingestion-etl) for how the existing three connectors are wired up.

### Adding a new NL-to-SQL provider

If the provider speaks the OpenAI Chat Completions format (most do), this is a two-step change:

1. Add an API key field to `Settings` in `app/config.py`:
   ```python
   newprovider_api_key: str | None = None
   ```
2. Add an entry to `PROVIDERS` in `app/utils/nl_to_sql.py`:
   ```python
   "newprovider": ProviderSpec(
       "newprovider", "New Provider", "openai", "https://api.newprovider.com/v1",
       ("model-a", "model-b"),
       "newprovider_api_key",
   ),
   ```

No other code needs to change — `available_providers()`, `generate_sql()`, and the UI dropdown all read from this registry. See [docs/nl-to-sql.md#adding-a-new-provider](docs/nl-to-sql.md#adding-a-new-provider) for the full walkthrough.

### Improving NL-to-SQL accuracy

Accuracy depends entirely on `SCHEMA_DOC` in `app/utils/nl_to_sql.py`. If you find a question that generates wrong SQL, the fix is almost always adding or clarifying something in that constant — it's where all platform business semantics are spelled out for the model.

### Database migrations

Schema changes use Alembic:

```bash
# After editing app/db/models.py, auto-generate a migration
make db-new-migration msg="describe your schema change"

# Review the generated file in alembic/versions/ before committing
make db-upgrade
```

Always review the autogenerated migration before committing — Alembic sometimes produces incorrect diffs for complex changes.

## Secret scanning

This repo runs [gitleaks](https://github.com/gitleaks/gitleaks) in CI on every push and PR (`.github/workflows/secret-scan.yml`). To run the same check locally before you push:

```bash
brew install gitleaks pre-commit   # or your platform's equivalent
pre-commit install
```

This installs a pre-commit hook (`.pre-commit-config.yaml`) that scans staged changes. If it flags a false positive, add a narrowly-scoped entry to `.gitleaks.toml` rather than disabling the hook entirely.

## Code of conduct

Be respectful and constructive. Disagreement about technical approach is welcome; personal attacks are not.

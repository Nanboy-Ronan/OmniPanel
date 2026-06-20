# Contributing to OmniPanel

Thanks for considering a contribution. This project is small and welcomes
issues and pull requests of any size — typo fixes, bug reports, new
platform connectors, and feature work are all welcome.

## Getting set up

Follow [docs/getting-started.md](docs/getting-started.md) to get the app
running locally, then [docs/testing.md](docs/testing.md) to run the test
suite. CI runs the same `pytest tests/ -q` against a throwaway PostgreSQL
database, so anything green locally should be green in CI.

## Before opening a PR

- Add or update tests for any behavior change. Prefer extending
  `tests/sample_dataset.py` over hardcoding new ad-hoc fixtures when a test
  needs order-shaped data.
- Run `python -m pytest tests/ -q` and make sure it passes.
- Keep PRs focused — one logical change per PR is easier to review than a
  bundle of unrelated fixes.

## Adding a new e-commerce platform connector

The detect → normalize → load pipeline in `app/db/etl/` is designed for
this: add a fingerprint to `detect_platform()`, a mapping in
`normalize_dataframe()`, and a raw table if you want to preserve the
platform-native row. See [docs/architecture.md](docs/architecture.md) for
how the existing three platforms are wired up.

## Adding a new NL-to-SQL provider

If the provider speaks the OpenAI Chat Completions format, this is a
two-line change. See
[docs/nl-to-sql.md → Adding a new provider](docs/nl-to-sql.md#adding-a-new-provider).

## Reporting bugs / requesting features

Open a GitHub issue. For bugs, include steps to reproduce and what you
expected vs. what happened. There's no SLA — this is maintained on a
best-effort basis.

## Code of conduct

Be respectful and constructive. Disagreement about technical approach is
fine; personal attacks are not.

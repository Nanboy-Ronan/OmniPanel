.PHONY: help api ui db-upgrade db-downgrade db-check db-new-migration db-history test test-unit deps-outdated deps-audit

PYTHON ?= python
ALEMBIC = $(PYTHON) -m alembic

help:
	@echo "Run locally:"
	@echo "  api                   Start the FastAPI backend (uvicorn --reload, :8000)"
	@echo "  ui                    Start the React workbench (:5173/console/)"
	@echo "  frontend-dev          Start the React console (:5173/console/)"
	@echo "  frontend-test         Run React console tests"
	@echo "  frontend-build        Type-check and build the React console"
	@echo ""
	@echo "Database migration targets:"
	@echo "  db-upgrade            Apply all pending migrations (alembic upgrade head)"
	@echo "  db-downgrade          Roll back one migration step (alembic downgrade -1)"
	@echo "  db-check              Verify database is at head; exits non-zero if not"
	@echo "  db-history            Show applied migration history"
	@echo "  db-new-migration msg=<description>"
	@echo "                        Auto-generate a new migration from ORM changes"
	@echo ""
	@echo "Other targets:"
	@echo "  test                  Run the full pytest test suite"
	@echo "  test-unit             Run only the WeChat Work unit tests (no database required)"
	@echo "  deps-outdated         List pinned requirements that have newer releases"
	@echo "  deps-audit            Scan pinned requirements for known CVEs (needs pipx)"
	@echo ""
	@echo "Dependency upgrade cadence and process: docs/maintenance.md"

api:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

ui: frontend-dev

db-upgrade:
	$(ALEMBIC) upgrade head

db-downgrade:
	$(ALEMBIC) downgrade -1

db-check:
	$(ALEMBIC) check

db-history:
	$(ALEMBIC) history --verbose

db-new-migration:
	@if [ -z "$(msg)" ]; then \
		echo "Usage: make db-new-migration msg=\"describe your schema change\""; \
		exit 1; \
	fi
	$(ALEMBIC) revision --autogenerate -m "$(msg)"
	@echo ""
	@echo "Review the generated file in alembic/versions/ before committing."
	@echo "Apply it with: make db-upgrade"

test:
	$(PYTHON) -m pytest tests/ -q

test-unit:
	$(PYTHON) -m pytest tests/test_wecom_unit.py -q

deps-outdated:
	$(PYTHON) -m pip list --outdated

deps-audit:
	pipx run pip-audit -r requirements.txt

.PHONY: frontend-dev frontend-test frontend-build
frontend-dev:
	npm --prefix frontend run dev

frontend-test:
	npm --prefix frontend test

frontend-build:
	npm --prefix frontend run build

API_DIR := apps/api
WEB_DIR := apps/web
PYTHON ?= python3.12
PNPM ?= pnpm
SEED_USERNAME ?= vitalybulyzhin@gmail.com
VENV := $(API_DIR)/.venv

.PHONY: help api-install api-migrate api-run checkout-worker demo-merchant-run api-lint seed-demo seed-checkout-demo web-install web-run web-lint web-typecheck web-build lint test

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "%-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

api-install: ## Create the API virtualenv and install development dependencies.
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(VENV)/bin/python -m pip install -e '$(API_DIR)[dev]'

api-migrate: ## Apply all API database migrations.
	@cd $(API_DIR) && .venv/bin/python -m alembic upgrade head

api-run: ## Run the API with hot reload on port 8000.
	@cd $(API_DIR) && .venv/bin/python -m uvicorn ag_platform_api.main:app --reload

checkout-worker: ## Run the trusted managed-checkout worker.
	@cd $(API_DIR) && .venv/bin/python -m ag_platform_api.checkout_worker

demo-merchant-run: ## Run the Stripe test merchant on port 8100 (development only).
	@cd $(API_DIR) && .venv/bin/python -m uvicorn ag_platform_api.demo_merchant:app --port 8100 --reload

api-lint: ## Run backend static checks.
	@cd $(API_DIR) && .venv/bin/python -m ruff check .
	@cd $(API_DIR) && .venv/bin/python -m ruff format --check .

seed-demo: ## Seed repeatable demo data for SEED_USERNAME.
	@cd $(API_DIR) && .venv/bin/python scripts/seed_demo_data.py --username "$(SEED_USERNAME)"

seed-checkout-demo: ## Add Stripe test success/decline/3DS methods to an existing user and agents.
	@cd $(API_DIR) && .venv/bin/python scripts/seed_checkout_demo.py --username "$(SEED_USERNAME)"

web-install: ## Install the web application dependencies.
	@cd $(WEB_DIR) && $(PNPM) install

web-run: ## Run the Next.js web application on port 3000.
	@cd $(WEB_DIR) && $(PNPM) dev

web-lint: ## Run web ESLint checks.
	@cd $(WEB_DIR) && $(PNPM) lint

web-typecheck: ## Run the web TypeScript compiler without emitting files.
	@cd $(WEB_DIR) && $(PNPM) exec tsc --noEmit

web-build: ## Build the production web application.
	@cd $(WEB_DIR) && $(PNPM) build

lint: api-lint web-lint web-typecheck ## Run backend and web static checks.

test: ## Run backend automated tests.
	@cd $(API_DIR) && .venv/bin/python -m pytest

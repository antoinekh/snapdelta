.DEFAULT_GOAL := help
.PHONY: help test test-postgres lint format assets

help: ## Show the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

test: ## Run the tests of every package
	uv run pytest -q

test-postgres: ## Run the tests of the PostgreSQL store against a server in a container (docker)
	scripts/test-postgres.sh

lint: ## Check the code: ruff and pyright
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

format: ## Format the code
	uv run ruff format .

assets: ## Update htmx, the fonts and Swagger UI (HTMX_VERSION=, SWAGGER_VERSION=, FONTS_VERSION=)
	scripts/update-assets.sh

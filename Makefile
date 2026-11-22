SHELL   := /bin/bash
COMPOSE := docker compose
VENV    := $(CURDIR)/.venv
PY      := $(VENV)/bin/python
SVC     := services/url-service
N       ?= 2

-include .env
export HTTP_PORT ?= 8080
export BASE_URL  ?= http://localhost:$(HTTP_PORT)

.DEFAULT_GOAL := help
.PHONY: help env install up down nuke logs ps psql scale test test-unit test-integration test-api lint fmt

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-17s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it does not exist
	@test -f .env || { cp .env.example .env; echo "Created .env from .env.example"; }

install: ## Install Python dependencies into .venv
	@test -x $(PY) || python3 -m venv $(VENV)
	$(PY) -m pip install -q -r $(SVC)/requirements-dev.txt

up: env ## Build and start the core stack, wait until healthy
	$(COMPOSE) up -d --build --wait
	@echo "Platform: $(BASE_URL)   (health: $(BASE_URL)/health/ready, docs: $(BASE_URL)/api/docs)"

down: ## Stop and remove containers (keeps data volumes)
	$(COMPOSE) down

nuke: ## Stop and remove containers AND data volumes
	$(COMPOSE) down -v --remove-orphans

logs: ## Follow logs (S=<service> for one service)
	$(COMPOSE) logs -f --tail=100 $(S)

ps: ## Show container status
	$(COMPOSE) ps -a

psql: ## Open psql on the primary database
	$(COMPOSE) exec postgres-primary psql -U app -d urlshortener

scale: ## Scale url-service replicas (N=3)
	$(COMPOSE) up -d --no-recreate --wait --scale url-service=$(N)

test: test-unit test-integration ## Unit + integration tests (integration needs `make up`)

test-unit: ## Unit tests (no Docker needed)
	cd $(SVC) && $(PY) -m pytest tests/unit

test-integration: ## Integration tests against the Compose PostgreSQL
	cd $(SVC) && $(PY) -m pytest tests/integration

test-api: env ## Restart the stack with APP_ENV=test and run API tests through Nginx
	APP_ENV=test $(COMPOSE) up -d --build --wait
	# APP_ENV is also passed to pytest so stack restores inside tests keep test mode.
	cd $(SVC) && APP_ENV=test $(PY) -m pytest tests/api $(ARGS)

lint: ## Lint and check formatting
	cd $(SVC) && $(VENV)/bin/ruff check . && $(VENV)/bin/ruff format --check .

fmt: ## Auto-format code
	cd $(SVC) && $(VENV)/bin/ruff check --fix . && $(VENV)/bin/ruff format .

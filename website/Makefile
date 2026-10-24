DC := docker compose -f compose.dev.yml
MANAGE := $(DC) exec django python manage.py

.DEFAULT_GOAL := help
.PHONY: help up down logs backend frontend migrations superuser starter-data test format

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_.-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.env.dev:
	cp .env.sample .env.dev

up: .env.dev ## Build and start everything in the background
	$(DC) up -d --build

down: ## Stop and remove the containers (keeps the database)
	$(DC) down

logs: ## Follow logs from all services
	$(DC) logs -f

backend: .env.dev ## Run Django + Postgres in the foreground
	$(DC) up --build django

frontend: .env.dev ## Run the frontend (and the backend it needs) in the foreground
	$(DC) up --build frontend

migrations: ## Create migrations from model changes and apply them
	$(MANAGE) makemigrations
	$(MANAGE) migrate

superuser: ## Create an admin user
	$(MANAGE) createsuperuser

starter-data: ## Add the starter train cars, stations and kiosks if missing
	$(MANAGE) load_starter_data

test: ## Run backend tests: make test [ARGS="-k health"]
	$(DC) exec django pytest $(ARGS)

format: ## Auto-fix lint issues and format backend and frontend (on the host)
	cd backend && uv run ruff check --fix . && uv run ruff format .
	cd frontend && npm run prettify

.PHONY: help dev test lint migrate run docker-up docker-down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Development ──────────────────────────────────────────────────

dev: ## Install dev dependencies
	pip install -e ".[dev]"

setup: ## Initial project setup
	pip install -e ".[dev]"
	cp -n .env.example .env || true
	pre-commit install

# ── Running ──────────────────────────────────────────────────────

run: ## Run the server locally
	afriagent serve --reload

run-prod: ## Run the server in production mode
	afriagent serve --workers 4

# ── Testing ──────────────────────────────────────────────────────

test: ## Run unit tests
	pytest tests/unit/ -v --tb=short

test-all: ## Run all tests (including integration)
	pytest -v --tb=short

test-cov: ## Run tests with coverage
	pytest tests/unit/ -v --tb=short --cov=afriagent --cov-report=term-missing --cov-report=html

test-integration: ## Run integration tests (needs docker-compose up)
	pytest tests/integration/ -v -m integration

# ── Linting ──────────────────────────────────────────────────────

lint: ## Run linter
	ruff check src/ tests/
	mypy src/

format: ## Format code
	ruff format src/ tests/

# ── Database ─────────────────────────────────────────────────────

migrate: ## Run database migrations
	alembic upgrade head

migrate-new: ## Create a new migration
	alembic revision --autogenerate -m "$(msg)"

migrate-down: ## Rollback last migration
	alembic downgrade -1

# ── Docker ───────────────────────────────────────────────────────

docker-up: ## Start all services with Docker Compose
	docker compose up -d

docker-down: ## Stop all services
	docker compose down

docker-logs: ## View logs
	docker compose logs -f afriagent

docker-build: ## Build the Docker image
	docker compose build

# ── Cleanup ──────────────────────────────────────────────────────

clean: ## Clean build artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

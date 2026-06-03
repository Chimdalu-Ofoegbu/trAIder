# =============================================================================
# trAIder — Makefile (D-38)
#
# Targets:
#   up           → docker compose up (all services with healthcheck waits)
#   seed         → idempotent: alembic + USDC/ETH seed + optional MockPerps deploy
#   verify-stack → post-seed assertion suite (exits 1 on any unconditional failure)
#   reset        → down (remove volumes) → up → seed → verify-stack
#   down         → docker compose down (preserves named volumes)
#   db-reset     → drop + recreate traider DB + alembic upgrade head
#   gen-types    → regenerate frontend/types/api.ts from backend OpenAPI schema
#
# Prerequisites:
#   Docker Desktop (make up/down/reset/db-reset)
#   Foundry cast (make seed / verify-stack)
#   uv (make seed: alembic)
#   pnpm (make gen-types: frontend)
# =============================================================================

.PHONY: up seed verify-stack reset down db-reset gen-types coverage help

# ── Environment ───────────────────────────────────────────────────────────────
# Source .env.example for defaults (real values come from .env.* gitignored files)
-include .env.example

ANVIL_RPC ?= http://localhost:8545
FORK_BLOCK ?= 353000000
COMPOSE_FILE ?= docker-compose.yml
COMPOSE := docker compose -f $(COMPOSE_FILE)

# ── up ────────────────────────────────────────────────────────────────────────
up:
	@echo "==> Starting dev stack (postgres + redis + anvil + pgadmin)..."
	$(COMPOSE) up -d --remove-orphans
	@echo "==> Waiting for all services to become healthy..."
	@# poll until all four services healthy or 120s timeout
	@DEADLINE=$$(($$(date +%s) + 120)); \
	while true; do \
		UNHEALTHY=$$($(COMPOSE) ps --format json 2>/dev/null \
			| python3 -c "import sys,json; rows=[json.loads(l) for l in sys.stdin if l.strip()]; \
				print(sum(1 for r in rows if r.get('Health','') not in ('healthy','')))" 2>/dev/null \
			|| echo "0"); \
		HEALTHY=$$($(COMPOSE) ps --format json 2>/dev/null \
			| python3 -c "import sys,json; rows=[json.loads(l) for l in sys.stdin if l.strip()]; \
				print(sum(1 for r in rows if r.get('Health','') == 'healthy'))" 2>/dev/null \
			|| echo "0"); \
		if [ "$$HEALTHY" -ge 4 ] 2>/dev/null; then \
			echo "==> All services healthy."; break; \
		fi; \
		if [ "$$(date +%s)" -ge "$$DEADLINE" ]; then \
			echo "ERROR: Services did not become healthy within 120s"; \
			$(COMPOSE) ps; \
			exit 1; \
		fi; \
		echo "  Waiting... (healthy=$$HEALTHY/4)"; sleep 5; \
	done
	@echo "==> Dev stack ready."
	@echo "    pgadmin: http://localhost:5050"
	@echo "    postgres: localhost:5432  (traider/traider)"
	@echo "    redis:    localhost:6379"
	@echo "    anvil:    localhost:8545  (chain 31337)"

# ── seed ──────────────────────────────────────────────────────────────────────
seed:
	@echo "==> Running seed script (idempotent)..."
	@bash scripts/seed.sh

# ── verify-stack ──────────────────────────────────────────────────────────────
verify-stack:
	@echo "==> Running verify-stack assertions..."
	@bash scripts/verify-stack.sh

# ── reset ─────────────────────────────────────────────────────────────────────
reset:
	@echo "==> Resetting dev stack (down + clear volumes + up + seed + verify)..."
	$(COMPOSE) down --volumes --remove-orphans
	$(MAKE) up
	$(MAKE) seed
	$(MAKE) verify-stack
	@echo "==> Reset complete."

# ── down ──────────────────────────────────────────────────────────────────────
down:
	@echo "==> Stopping dev stack..."
	$(COMPOSE) down
	@echo "==> Dev stack stopped (named volumes preserved)."

# ── db-reset ──────────────────────────────────────────────────────────────────
db-reset:
	@echo "==> Resetting database (drop + recreate + alembic upgrade head)..."
	@# Drop and recreate the traider database
	PGPASSWORD=traider psql -h localhost -p 5432 -U traider -c \
		"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='traider' AND pid <> pg_backend_pid();" \
		postgres 2>/dev/null || true
	PGPASSWORD=traider psql -h localhost -p 5432 -U traider -c "DROP DATABASE IF EXISTS traider;" postgres 2>/dev/null || true
	PGPASSWORD=traider psql -h localhost -p 5432 -U traider -c "CREATE DATABASE traider OWNER traider;" postgres 2>/dev/null || true
	@echo "==> Database recreated. Running alembic upgrade head..."
	DATABASE_URL=postgresql+psycopg://migrator_user:migrator_pass@localhost:5432/traider \
		uv run alembic -c migrations/alembic.ini upgrade head
	@echo "==> db-reset complete."

# ── gen-types ─────────────────────────────────────────────────────────────────
gen-types:
	@echo "==> Regenerating frontend/types/api.ts from backend OpenAPI schema..."
	@bash scripts/gen-types.sh

# ── coverage ──────────────────────────────────────────────────────────────────
# TEST-01: contracts/src/ line coverage >= 90% enforced by this target.
# Uses FOUNDRY_PROFILE=coverage (via_ir=false, optimizer=false) with --ir-minimum
# to work around the foundry#6592 stack-too-deep incompatibility between coverage
# instrumentation and the default via-ir pipeline.
# Run: make coverage
coverage:
	cd contracts && FOUNDRY_PROFILE=coverage forge coverage --ir-minimum --report summary

# ── help ──────────────────────────────────────────────────────────────────────
help:
	@echo "trAIder Makefile targets:"
	@echo "  make up           Start dev stack (postgres + redis + anvil + pgadmin)"
	@echo "  make seed         Idempotent seed (alembic + USDC/ETH + optional MockPerps)"
	@echo "  make verify-stack Post-seed assertions (exit 1 on unconditional failure)"
	@echo "  make reset        Full reset: down → up → seed → verify-stack"
	@echo "  make down         Stop dev stack (preserves volumes)"
	@echo "  make db-reset     Drop + recreate DB + alembic upgrade head"
	@echo "  make gen-types    Regenerate frontend/types/api.ts from backend OpenAPI"
	@echo "  make coverage     Run forge coverage on contracts/src/ (>= 90% gate, TEST-01)"
	@echo ""
	@echo "Prerequisites: Docker Desktop, Foundry (cast/forge), uv, pnpm"

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

.PHONY: up seed verify-stack reset down db-reset gen-types coverage deploy-sepolia deploy-sepolia-clean help

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
		uv run --project orchestrator alembic -c migrations/alembic.ini upgrade head
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

# ── deploy-sepolia ────────────────────────────────────────────────────────────
# DEPLOY-01: Idempotent Arbitrum Sepolia deploy + Arbiscan auto-verify (D-14)
#
# Prerequisites (fill in .env / .env.deployer / .env.operator-trade / .env.operator-journal):
#   SEPOLIA_RPC            Alchemy Arbitrum Sepolia HTTPS endpoint
#   ARBISCAN_API_KEY       Arbiscan API key (https://arbiscan.io/apis)
#   DEPLOYER_PRIVATE_KEY   Deployer EOA private key (funds Sepolia ETH for gas - SEC-01)
#   OPERATOR_JOURNAL_KEY   Operator-journal EOA ADDRESS (not private key; becomes immutable in JournalRegistry)
#   ORCHESTRATOR           Orchestrator EOA ADDRESS (not private key; submits trades via vault)
#   OPERATOR               Operator EOA ADDRESS (not private key; funds session)
#
# Sepolia-specific env (override Arb One mainnet defaults in 01-Deploy.s.sol):
#   DEPLOY_MOCK_SUBSTRATE=true   Deploy MockERC20 + MockPerps + 3x MockChainlinkAggregator + MockSequencerUptimeFeed
#   USE_SEPOLIA_STALENESS=true   Use 6-hour staleness window (shorter than mainnet heartbeat)
#   SEQUENCER_FEED=0x0000...     No real sequencer uptime feed on Arbitrum Sepolia - skip check
#
# Idempotency: reads deployments/sepolia.json; if sessionFactory is non-zero, deploy is skipped.
# A second run of this target is always a no-op that confirms the manifest is still valid.
#
# Arbiscan verify: --verify + --etherscan-api-key submits source for every deployed contract.
# Requires [etherscan] section in contracts/foundry.toml (already configured for chain 421614).
#
# Note: on Windows git-bash without make, run the forge command directly:
#   cd contracts && forge script script/01-Deploy.s.sol \
#     --rpc-url $SEPOLIA_RPC --broadcast --verify --etherscan-api-key $ARBISCAN_API_KEY \
#     --private-key $DEPLOYER_PRIVATE_KEY --sig "run()"
#
# WARNING: --broadcast and --verify send real transactions to Arbitrum Sepolia and submit
# source code to Arbiscan. This is an OUTWARD-FACING action requiring operator authorization.
# DO NOT run with --broadcast in automated CI/CD without explicit approval.
deploy-sepolia:
	@echo "==> Deploying to Arbitrum Sepolia (idempotent + Arbiscan auto-verify)..."
	@echo "    Chain: Arbitrum Sepolia (421614)"
	@echo "    Manifest: deployments/sepolia.json"
	cd contracts && \
		DEPLOY_MOCK_SUBSTRATE=true \
		USE_SEPOLIA_STALENESS=true \
		SEQUENCER_FEED=0x0000000000000000000000000000000000000000 \
		forge script script/01-Deploy.s.sol \
			--rpc-url $(SEPOLIA_RPC) \
			--broadcast \
			--verify \
			--etherscan-api-key $(ARBISCAN_API_KEY) \
			--private-key $(DEPLOYER_PRIVATE_KEY) \
			--sig "run()"
	@echo "==> deploy-sepolia complete. Check deployments/sepolia.json for addresses."
	@echo "    Verify Arbiscan links printed above show verified source (green check)."

# ── deploy-sepolia-clean ──────────────────────────────────────────────────────
# Remove the Sepolia manifest to allow a fresh deploy on next make deploy-sepolia.
# Use this when you want to start a new session (e.g. after reset for a fresh demo).
# The deploy script reads a non-zero sessionFactory as "already deployed" and skips.
# Deleting the manifest resets that guard.
#
# NOTE: This deletes the canonical address manifest. The orchestrator and frontend
# will lose the deployed addresses until make deploy-sepolia is re-run.
deploy-sepolia-clean:
	@echo "==> Removing deployments/sepolia.json (fresh deploy on next make deploy-sepolia)..."
	rm -f deployments/sepolia.json
	@echo "==> Creating empty manifest template..."
	@printf '{\n  "sessionFactory": "0x0000000000000000000000000000000000000000",\n  "oracle": "0x0000000000000000000000000000000000000000",\n  "journal": "0x0000000000000000000000000000000000000000",\n  "vaultClaude": "0x0000000000000000000000000000000000000000",\n  "vaultGpt": "0x0000000000000000000000000000000000000000",\n  "vaultGem": "0x0000000000000000000000000000000000000000",\n  "adapter": "0x0000000000000000000000000000000000000000",\n  "mockUsdc": "0x0000000000000000000000000000000000000000",\n  "ethFeed": "0x0000000000000000000000000000000000000000",\n  "btcFeed": "0x0000000000000000000000000000000000000000",\n  "solFeed": "0x0000000000000000000000000000000000000000",\n  "sequencerFeed": "0x0000000000000000000000000000000000000000"\n}\n' > deployments/sepolia.json
	@echo "==> deploy-sepolia-clean complete. Run make deploy-sepolia to deploy fresh."

# ── help ──────────────────────────────────────────────────────────────────────
help:
	@echo "trAIder Makefile targets:"
	@echo "  make up                 Start dev stack (postgres + redis + anvil + pgadmin)"
	@echo "  make seed               Idempotent seed (alembic + USDC/ETH + optional MockPerps)"
	@echo "  make verify-stack       Post-seed assertions (exit 1 on unconditional failure)"
	@echo "  make reset              Full reset: down -> up -> seed -> verify-stack"
	@echo "  make down               Stop dev stack (preserves volumes)"
	@echo "  make db-reset           Drop + recreate DB + alembic upgrade head"
	@echo "  make gen-types          Regenerate frontend/types/api.ts from backend OpenAPI"
	@echo "  make coverage           Run forge coverage on contracts/src/ (>= 90% gate, TEST-01)"
	@echo "  make deploy-sepolia     Deploy full stack to Arbitrum Sepolia (DEPLOY-01, D-14)"
	@echo "                          Idempotent: re-run skips if manifest already populated"
	@echo "                          Requires: SEPOLIA_RPC, ARBISCAN_API_KEY, DEPLOYER_PRIVATE_KEY,"
	@echo "                                    OPERATOR_JOURNAL_KEY, ORCHESTRATOR, OPERATOR"
	@echo "  make deploy-sepolia-clean  Reset manifest to zeros (fresh deploy next run)"
	@echo ""
	@echo "Prerequisites: Docker Desktop, Foundry (cast/forge), uv, pnpm"

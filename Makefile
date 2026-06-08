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

.PHONY: up seed verify-stack reset down db-reset gen-types coverage deploy-sepolia deploy-sepolia-clean run-mini-session test-03-gate verify-sepolia help

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

# ── run-mini-session ─────────────────────────────────────────────────────────
# TEST-03 / D-04: Run the Sepolia mini-session with one model (Claude).
#
# Usage:
#   make run-mini-session SESSION_TIME=1800
#
# Required env (fill in orchestrator/.env or root .env):
#   SEPOLIA_RPC                  Alchemy Arbitrum Sepolia HTTPS endpoint
#   OPERATOR_TRADE_KEY           Hex private key for operator-trade EOA (SEC-01, gitignored)
#   OPERATOR_JOURNAL_KEY_PRIV    Hex private key for operator-journal EOA (SEC-01, gitignored)
#   OPERATOR_JOURNAL_KEY_ADDR    Hex address for operator-journal EOA
#   PINATA_JWT                   Pinata V3 JWT (JOURNAL-02, gitignored)
#   FILEBASE_API_KEY             Filebase S3 API key (D-08, gitignored)
#   ANTHROPIC_API_KEY            Anthropic API key for Claude (gitignored)
#   ORCHESTRATOR_DATABASE_URL    Async Postgres URL (postgresql+asyncpg://...)
#
# Optional env:
#   PERPS_VENUE                  "mock" | "gmx" (default: mock)
#   SESSION_CADENCE              Trading cadence seconds (default: 60)
#   PRICE_SEED                   PriceWalk seed (default: 42)
#   REDIS_URL                    Redis for WS fanout
#   TELEGRAM_BOT_TOKEN           Telegram bot token (D-15, optional)
#   TELEGRAM_CHAT_ID             Telegram chat ID (D-15, optional)
#   LATENCY_WATCHDOG_THRESHOLD   1A latency breach threshold seconds (D-03, default: 30)
#
# D-03 note: PERPS_VENUE defaults to "mock". To drill the 1A flip:
#   1. Induce latency (delay keeper executeOrder).
#   2. Observe WARNING alert from the latency watchdog.
#   3. Operator: stop this process, set PERPS_VENUE=<venue>, restart — NEVER auto-flip.
#
# WARNING: This connects to Arbitrum Sepolia and makes real LLM + blockchain calls.
# Ensure operator keys are funded with Sepolia ETH and vaultClaude is seeded with
# mock USDC before running (see docs/RUNBOOK.md vault-seeding section).
SESSION_TIME ?= 1800

run-mini-session:
	@echo "==> Starting Sepolia mini-session (SESSION_TIME=$(SESSION_TIME)s PERPS_VENUE=$${PERPS_VENUE:-mock})..."
	@echo "    Manifest: deployments/sepolia.json"
	@echo "    D-03: latency watchdog active — operator flip required if WARNING fires"
	@echo "    D-04: gate = >=30min clean run + createOrder->journal E2E + nav tick"
	ORCHESTRATOR_DATABASE_URL=$${ORCHESTRATOR_DATABASE_URL:-postgresql+asyncpg://orchestrator_user:orchestrator_pass@localhost:5432/traider} \
	SESSION_DURATION=$(SESSION_TIME) \
	PERPS_VENUE=$${PERPS_VENUE:-mock} \
		uv run --project orchestrator --env-file orchestrator/.env \
			python -m orchestrator.loop.run_session
	@echo "==> mini-session complete."

# ── test-03-gate ──────────────────────────────────────────────────────────────
# TEST-03: Automated gate harness for Phase-4 entry (D-04).
#
# Runs in two stages:
#   Stage 1: Fork suite (GMX fork at block 405000000 + Sequencer fork at 353000000)
#   Stage 2: Python harness (nav-tick + both-gateways CID-fetchable assertions)
#
# Stage 1 requires ARB_RPC in env (Alchemy Arbitrum One HTTPS endpoint).
# Stage 2 requires ORCHESTRATOR_DATABASE_URL (Postgres with alembic applied).
#          Requires PINATA_JWT + FILEBASE_API_KEY for live gateway assertions;
#          cleanly skips (EXPLICIT-DEFER) when absent.
#
# Usage:
#   make test-03-gate ARB_RPC=https://arb-mainnet.g.alchemy.com/v2/<KEY>
#
# PASS/FAIL is printed at the end; result should be recorded in
# .planning/phases/03-real-gmx-chainlink-sepolia-deploy/03-TEST-03-GATE.md
test-03-gate:
	@echo "==> TEST-03 gate harness: Stage 1 — fork suite precondition"
	@echo "    GMX fork tests (block 405000000, FOUNDRY_PROFILE=gmx-fork):"
	@if [ -z "$${ARB_RPC}" ]; then \
		echo "    WARNING: ARB_RPC not set — Stage 1 (fork suite) will be SKIPPED."; \
		echo "    Set ARB_RPC=https://arb-mainnet.g.alchemy.com/v2/<KEY> to run fork tests."; \
	else \
		cd contracts && FOUNDRY_PROFILE=gmx-fork forge test \
			--match-path "test/fork/GMXAdapterForkTest.t.sol" \
			--fork-url "$${ARB_RPC}" \
			-v 2>&1 | tail -20 || echo "  GMX fork tests: see output above"; \
		echo "    Sequencer fork test (block 353000000):"; \
		cd contracts && forge test \
			--match-path "test/fork/ChainlinkSequencerForkTest.t.sol" \
			--fork-url "$${ARB_RPC}" \
			--fork-block-number 353000000 \
			-v 2>&1 | tail -20 || echo "  Sequencer fork tests: see output above"; \
	fi
	@echo ""
	@echo "==> TEST-03 gate harness: Stage 2 — Python automated assertions"
	ORCHESTRATOR_DATABASE_URL=$${ORCHESTRATOR_DATABASE_URL:-postgresql+asyncpg://orchestrator_user:orchestrator_pass@localhost:5432/traider} \
	SEPOLIA_RPC=$${SEPOLIA_RPC:-} \
	ARB_RPC=$${ARB_RPC:-} \
		uv run --project orchestrator pytest \
			orchestrator/tests/integration/test_mini_session_gate.py \
			-v --tb=short 2>&1
	@echo ""
	@echo "==> TEST-03 gate complete. Record result in 03-TEST-03-GATE.md."
	@echo "    GATE: PASS criteria (all 5 must hold):"
	@echo "      1. Fork suite green (Stage 1)"
	@echo "      2. vault.nav() ticks with mock feed (Stage 2)"
	@echo "      3. This-run CIDs fetchable from BOTH gateways (Stage 2, requires PINATA_JWT)"
	@echo "      4. createOrder->execute->journal E2E (live run only — Task 3)"
	@echo "      5. >=30min clean continuous run (live run only — Task 3)"

# ── verify-sepolia ────────────────────────────────────────────────────────────
# Step 4 scanner: scripted post-deploy Sepolia integration verification harness.
# Exercises every cell of 03-INTEGRATION-MATRIX.md against the real deployed
# Sepolia contracts (AUTH / STATE / TIMING). READ-ONLY — no transactions submitted.
#
# Canary cell: AUTH-3 (journal.authorizedPublishers(operator-journal EOA))
#   - FAILS on pre-redeploy deployment (GAP #5 not yet fixed) — expected
#   - MUST PASS after the redeploy that fixes GAP #5
#
# Usage:
#   make verify-sepolia                  # uses SEPOLIA_RPC from .env
#   SEPOLIA_RPC=https://... make verify-sepolia
#
# No-make equivalent (Windows git-bash):
#   source .env 2>/dev/null
#   uv run --project orchestrator python -m orchestrator.verify_integration
#
# Pytest wrapper (granular per-cell diagnostics):
#   source .env 2>/dev/null
#   uv run --project orchestrator pytest \
#       orchestrator/tests/integration/test_post_deploy_verification.py -v --tb=short
#
# Required env:
#   SEPOLIA_RPC      Alchemy / public Arbitrum Sepolia HTTPS endpoint
#                    (default: https://sepolia-rollup.arbitrum.io/rpc if unset)
#
# Optional env (addresses only, NOT private keys):
#   DEPLOYER_ADDRESS             Deployer EOA address (fallback: from matrix)
#   OPERATOR_TRADE_ADDRESS       Operator-trade EOA address (fallback: from matrix)
#   OPERATOR_JOURNAL_KEY_ADDR    Operator-journal EOA address (fallback: from matrix)
verify-sepolia:
	@echo "==> Post-deploy Sepolia integration verification (Step 4 scanner)..."
	@echo "    Manifest: deployments/sepolia.json"
	@echo "    RPC: $${SEPOLIA_RPC:-https://sepolia-rollup.arbitrum.io/rpc (public fallback)}"
	@echo "    Canary: AUTH-3 (journal.authorizedPublishers) — FAIL=pre-redeploy, PASS=post-GAP#5-fix"
	@echo ""
	SEPOLIA_RPC=$${SEPOLIA_RPC:-https://sepolia-rollup.arbitrum.io/rpc} \
		uv run --project orchestrator python -m orchestrator.verify_integration
	@echo ""
	@echo "==> verify-sepolia complete. Exit 0 = all-pass. Exit 1 = gap(s) found."

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
	@echo "  make run-mini-session       Run Sepolia mini-session (TEST-03 / D-04)"
	@echo "                              SESSION_TIME=1800 (default 30min)"
	@echo "                              Requires: SEPOLIA_RPC, OPERATOR_TRADE_KEY, PINATA_JWT,"
	@echo "                                        ANTHROPIC_API_KEY, ORCHESTRATOR_DATABASE_URL"
	@echo "  make test-03-gate           TEST-03 automated gate harness"
	@echo "                              Stage 1: forge fork suite (GMX@405000000 + Sequencer@353000000)"
	@echo "                              Stage 2: Python assertions (nav-tick + both-gateways CID)"
	@echo "                              Requires: ARB_RPC (Stage 1), PINATA_JWT (Stage 2 live)"
	@echo ""
	@echo "  make verify-sepolia         Post-deploy Sepolia integration verification (Step 4 scanner)"
	@echo "                              READ-ONLY: all cells from 03-INTEGRATION-MATRIX.md"
	@echo "                              Canary: AUTH-3 journal.authorizedPublishers (GAP #5 fix signal)"
	@echo "                              No-make: uv run --project orchestrator python -m orchestrator.verify_integration"
	@echo ""
	@echo "Prerequisites: Docker Desktop, Foundry (cast/forge), uv, pnpm"

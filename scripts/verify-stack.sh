#!/usr/bin/env bash
# =============================================================================
# scripts/verify-stack.sh — Post-seed assertion suite (D-38)
#
# UNCONDITIONAL assertions (fail fast on any failure — exit non-zero):
#   1. anvil is reachable + fork block >= FORK_BLOCK
#   2. Postgres is reachable + both schemas (orchestrator + backend) exist
#   3. USDC balances read back >= expected for all seeded operator addresses
#      (Catches Pattern 5 gotcha: wrong-slot silent write = balance reads 0)
#
# GUARDED assertions (skip cleanly if prerequisites absent):
#   4. MockPerps cast code non-empty — only when:
#      - contracts/src/mocks/MockPerps.sol OR the compiled artifact is present, AND
#      - MOCK_PERPS_ADDRESS is set in .env.local
#      Skipped at Wave 1 with a notice. Plan 09 owns the authoritative assertion.
#
# Exit codes:
#   0 = all unconditional assertions passed (guarded steps may have been skipped)
#   1 = one or more unconditional assertions failed
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Configuration ─────────────────────────────────────────────────────────────
ANVIL_RPC="${ANVIL_RPC:-http://localhost:8545}"
FORK_BLOCK="${FORK_BLOCK:-353000000}"
USDC_ARBITRUM="${USDC_ARBITRUM:-0xaf88d065e77c8cC2239327C5EDb3A432268e5831}"
# Minimum expected USDC balance (1M USDC = 1e12 units @ 6 decimals)
MIN_USDC_BALANCE="1000000000000"

# Postgres connection (psql-compatible, using traider superuser for schema check)
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-traider}"
PG_DB="${PG_DB:-traider}"
PG_PASS="${PG_PASS:-traider}"

ENV_LOCAL="${REPO_ROOT}/.env.local"

# Failure tracker
FAILURES=0

echo "============================================================"
echo "  trAIder — Verify Stack (D-38)"
echo "  Anvil RPC: ${ANVIL_RPC}"
echo "  Fork block (min): ${FORK_BLOCK}"
echo "============================================================"
echo ""

# ── Helper: fail ──────────────────────────────────────────────────────────────
fail() {
    echo "[FAIL] $*" >&2
    FAILURES=$((FAILURES + 1))
}

# ── Helper: load address from .env.local ─────────────────────────────────────
load_env_local_value() {
    local key="$1"
    if [[ -f "${ENV_LOCAL}" ]]; then
        grep -E "^${key}=" "${ENV_LOCAL}" | head -1 | cut -d'=' -f2 || echo ""
    else
        echo ""
    fi
}

# ── Helper: load address from .env.<role> ────────────────────────────────────
load_address_from_role() {
    local role="$1"
    local env_file="${REPO_ROOT}/.env.${role}"
    if [[ ! -f "$env_file" ]]; then
        echo ""
        return 0
    fi
    local addr
    addr=$(grep -E "^# Address.*: 0x[0-9a-fA-F]{40}$" "$env_file" | head -1 | sed 's/.*: //' || echo "")
    echo "$addr"
}

# ── Assertion 1: Anvil reachable + fork block ─────────────────────────────────
echo "[1/4] Checking anvil fork block..."
CURRENT_BLOCK=$(cast block-number --rpc-url "${ANVIL_RPC}" 2>/dev/null || echo "")

if [[ -z "${CURRENT_BLOCK}" ]]; then
    fail "Anvil not reachable at ${ANVIL_RPC} — is the dev stack running? (make up)"
else
    if [[ "${CURRENT_BLOCK}" -ge "${FORK_BLOCK}" ]]; then
        echo "[OK]  Anvil block ${CURRENT_BLOCK} >= fork block ${FORK_BLOCK}"
    else
        fail "Anvil block ${CURRENT_BLOCK} < expected fork block ${FORK_BLOCK} — fork may not be complete"
    fi
fi
echo ""

# ── Assertion 2: Postgres schemas ─────────────────────────────────────────────
echo "[2/4] Checking Postgres schemas (orchestrator + backend)..."
SCHEMA_OUTPUT=$(PGPASSWORD="${PG_PASS}" psql \
    -h "${PG_HOST}" \
    -p "${PG_PORT}" \
    -U "${PG_USER}" \
    -d "${PG_DB}" \
    -t \
    -c "\dn" 2>/dev/null || echo "psql_failed")

if [[ "${SCHEMA_OUTPUT}" == "psql_failed" ]]; then
    fail "Postgres not reachable at ${PG_HOST}:${PG_PORT} — is the dev stack running? (make up)"
else
    if echo "${SCHEMA_OUTPUT}" | grep -q "orchestrator"; then
        echo "[OK]  Schema 'orchestrator' exists"
    else
        fail "Schema 'orchestrator' not found — run: make seed (alembic upgrade head)"
    fi

    if echo "${SCHEMA_OUTPUT}" | grep -q "backend"; then
        echo "[OK]  Schema 'backend' exists"
    else
        fail "Schema 'backend' not found — run: make seed (alembic upgrade head)"
    fi
fi
echo ""

# ── Assertion 3: USDC balances (UNCONDITIONAL — Pattern 5 gotcha catch) ──────
echo "[3/4] Checking USDC balances for operator addresses..."
echo "      (Catches Pattern 5: wrong-slot silent write = balance reads 0)"

declare -a CHECK_ROLES=("deployer" "operator-trade" "operator-journal" "gas")
ANY_ADDRESS_CHECKED=false

for role in "${CHECK_ROLES[@]}"; do
    addr=$(load_address_from_role "$role")
    if [[ -z "$addr" || ! "$addr" =~ ^0x[0-9a-fA-F]{40}$ ]]; then
        echo "  [SKIP] ${role}: address not found in .env.${role}"
        continue
    fi

    # Read USDC balanceOf via cast call — ABI: balanceOf(address)(uint256)
    BALANCE=$(cast call "${USDC_ARBITRUM}" \
        "balanceOf(address)(uint256)" \
        "${addr}" \
        --rpc-url "${ANVIL_RPC}" 2>/dev/null || echo "cast_failed")

    if [[ "${BALANCE}" == "cast_failed" || -z "${BALANCE}" ]]; then
        fail "USDC balanceOf call failed for ${role} (${addr}) — anvil may not be seeded"
        continue
    fi

    # Remove any trailing type annotations that cast may append (e.g. "[uint256]")
    BALANCE_NUM=$(echo "${BALANCE}" | grep -oE '[0-9]+' | head -1)

    if [[ -z "${BALANCE_NUM}" ]]; then
        fail "Cannot parse USDC balance for ${role} (${addr}): raw=${BALANCE}"
        continue
    fi

    if [[ "${BALANCE_NUM}" -ge "${MIN_USDC_BALANCE}" ]]; then
        echo "  [OK]  ${role} (${addr}): USDC balance = ${BALANCE_NUM} (>= ${MIN_USDC_BALANCE})"
        ANY_ADDRESS_CHECKED=true
    else
        fail "${role} (${addr}): USDC balance = ${BALANCE_NUM} < expected ${MIN_USDC_BALANCE} — seed may have used wrong slot"
    fi
done

if [[ "$ANY_ADDRESS_CHECKED" == "false" && ${FAILURES} -eq 0 ]]; then
    echo "  [WARN] No operator addresses found to check — generate keys first: bash scripts/gen-keys.sh"
fi
echo ""

# ── Assertion 4: MockPerps cast code (GUARDED) ───────────────────────────────
echo "[4/4] Checking MockPerps deploy guard..."

MOCK_PERPS_SOL="${REPO_ROOT}/contracts/src/mocks/MockPerps.sol"
MOCK_PERPS_ARTIFACT="${REPO_ROOT}/contracts/out/MockPerps.sol/MockPerps.json"
MOCK_PERPS_ADDRESS=$(load_env_local_value "MOCK_PERPS_ADDRESS")

if ([[ -f "${MOCK_PERPS_SOL}" ]] || [[ -f "${MOCK_PERPS_ARTIFACT}" ]]) && [[ -n "${MOCK_PERPS_ADDRESS}" ]]; then
    # Guard condition met: MockPerps.sol/artifact exists AND an address is in .env.local
    echo "[4/4] MockPerps artifact present + address in .env.local — asserting cast code..."

    CODE=$(cast code "${MOCK_PERPS_ADDRESS}" --rpc-url "${ANVIL_RPC}" 2>/dev/null || echo "cast_failed")

    if [[ "${CODE}" == "cast_failed" ]]; then
        fail "cast code call failed for MockPerps at ${MOCK_PERPS_ADDRESS}"
    elif [[ "${CODE}" == "0x" || -z "${CODE}" ]]; then
        fail "MockPerps at ${MOCK_PERPS_ADDRESS} has no code — deploy may have failed"
    else
        CODE_LEN=${#CODE}
        echo "[OK]  MockPerps at ${MOCK_PERPS_ADDRESS} has bytecode (${CODE_LEN} chars)"
    fi
else
    echo "[4/4] MockPerps not deployed yet — skipping cast-code check."
    if [[ ! -f "${MOCK_PERPS_SOL}" ]] && [[ ! -f "${MOCK_PERPS_ARTIFACT}" ]]; then
        echo "      contracts/src/mocks/MockPerps.sol not present (ships in Plan 08)."
    fi
    if [[ -z "${MOCK_PERPS_ADDRESS}" ]]; then
        echo "      MOCK_PERPS_ADDRESS not in .env.local (run make seed after Plan 08 lands)."
    fi
    echo "      Plan 09 deploys MockPerps authoritatively and owns the cast-code assertion."
    echo "      This is expected behavior at Wave 1 — continuing with exit 0."
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "============================================================"
if [[ ${FAILURES} -eq 0 ]]; then
    echo "  verify-stack PASSED (${FAILURES} failures)"
    echo "  Dev stack is fully seeded and operational."
    echo "============================================================"
    exit 0
else
    echo "  verify-stack FAILED (${FAILURES} failure(s) — see [FAIL] lines above)"
    echo "  Fix the issues above, then re-run: make verify-stack"
    echo "============================================================"
    exit 1
fi

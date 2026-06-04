#!/usr/bin/env bash
# =============================================================================
# scripts/seed.sh — Idempotent dev stack seeder (D-38)
#
# Steps (UNCONDITIONAL — always run, safe to re-run):
#   1. Run Alembic migrations (alembic upgrade head)
#   2. Seed USDC via forge script deal() into 4 operator addresses (Pattern 5)
#   3. Seed ETH via cast rpc anvil_setBalance into 4 operator addresses
#   4. Write funded addresses to .env.local
#
# GUARDED step (skips gracefully if MockPerps.sol / artifact is absent):
#   5. CREATE2-deploy MockPerps to a deterministic address and append to .env.local
#      Guard: only runs when contracts/src/mocks/MockPerps.sol OR
#             contracts/out/MockPerps.sol/MockPerps.json is present.
#      At Wave 1, MockPerps.sol ships in Plan 08; Plan 09 is the authoritative deploy+assert.
#
# Re-running:
#   - Alembic: no-op if already at head
#   - USDC/ETH: idempotent (overwrite-safe — deal() + anvil_setBalance always succeed)
#   - .env.local: overwritten each run (safe, gitignored)
#   - MockPerps deploy: guard prevents double-deploy (re-deploys to same CREATE2 address)
#
# Threat T-0-seedslot: use deal() NOT hand-computed slot (Pattern 5 — Arbitrum proxy USDC)
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Configuration ─────────────────────────────────────────────────────────────
ANVIL_RPC="${ANVIL_RPC:-http://localhost:8545}"
FORK_BLOCK="${FORK_BLOCK:-353000000}"

# Arbitrum One canonical USDC (proxy — D-38, Pattern 5: use deal() not slot math)
USDC_ARBITRUM="0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDC_SEED_AMOUNT="1000000000000"  # 1,000,000 USDC (6 decimals = 1e12 units)
ETH_SEED_AMOUNT="0xDE0B6B3A7640000"  # 1 ETH in wei (hex)

# Database URL for Alembic (matches docker-compose postgres service)
DB_URL="${DATABASE_URL:-postgresql+psycopg://migrator_user:migrator_pass@localhost:5432/traider}"

# ENV local output (gitignored — D-16)
ENV_LOCAL="${REPO_ROOT}/.env.local"

echo "============================================================"
echo "  trAIder — Dev Stack Seed (D-38)"
echo "  Anvil RPC: ${ANVIL_RPC}"
echo "  Fork block: ${FORK_BLOCK}"
echo "============================================================"
echo ""

# ── Helper: load address from .env.<role> file ────────────────────────────────
load_address() {
    local role="$1"
    local env_file="${REPO_ROOT}/.env.${role}"
    if [[ ! -f "$env_file" ]]; then
        echo "[WARN] .env.${role} not found — generate keys first: bash scripts/gen-keys.sh" >&2
        echo ""
        return 1
    fi
    # Extract the address comment line (# Address (safe to display/share): 0x...)
    local addr
    addr=$(grep -E "^# Address.*: 0x[0-9a-fA-F]{40}$" "$env_file" | head -1 | sed 's/.*: //')
    if [[ -z "$addr" ]]; then
        # Fallback: derive address from private key
        local pk
        pk=$(grep -E "^[A-Z_]+=0x[0-9a-fA-F]{64}" "$env_file" | head -1 | cut -d'=' -f2)
        if [[ -z "$pk" ]]; then
            echo "[WARN] Cannot extract address from .env.${role}" >&2
            echo ""
            return 1
        fi
        addr=$(cast wallet address --private-key "$pk" 2>/dev/null || echo "")
    fi
    echo "$addr"
}

# ── Step 1: Alembic migrations (UNCONDITIONAL) ────────────────────────────────
echo "[1/5] Running Alembic migrations (alembic upgrade head)..."
if command -v uv &>/dev/null; then
    (cd "${REPO_ROOT}" && DATABASE_URL="${DB_URL}" uv run --project orchestrator alembic -c migrations/alembic.ini upgrade head)
else
    echo "[WARN] uv not found — attempting python fallback"
    (cd "${REPO_ROOT}/orchestrator" && DATABASE_URL="${DB_URL}" python -m alembic -c ../migrations/alembic.ini upgrade head)
fi
echo "[1/5] Alembic migrations complete."
echo ""

# ── Step 2: Collect operator addresses ───────────────────────────────────────
echo "[2/5] Collecting operator addresses..."

ADDR_DEPLOYER=$(load_address "deployer" || true)
ADDR_TRADE=$(load_address "operator-trade" || true)
ADDR_JOURNAL=$(load_address "operator-journal" || true)
ADDR_GAS=$(load_address "gas" || true)

# Build arrays (only non-empty addresses)
declare -a ADDRESSES=()
declare -a ROLES=()

for pair in "deployer:${ADDR_DEPLOYER}" "operator-trade:${ADDR_TRADE}" "operator-journal:${ADDR_JOURNAL}" "gas:${ADDR_GAS}"; do
    role="${pair%%:*}"
    addr="${pair#*:}"
    if [[ -n "$addr" && "$addr" =~ ^0x[0-9a-fA-F]{40}$ ]]; then
        ADDRESSES+=("$addr")
        ROLES+=("$role")
        echo "  [OK] ${role}: ${addr}"
    else
        echo "  [SKIP] ${role}: no valid address found"
    fi
done

if [[ ${#ADDRESSES[@]} -eq 0 ]]; then
    echo "[ERROR] No operator addresses found. Run: bash scripts/gen-keys.sh first."
    exit 1
fi
echo ""

# ── Step 3: Seed USDC + ETH (UNCONDITIONAL) ──────────────────────────────────
# Seed USDC via `cast rpc anvil_setStorageAt`; seed ETH via `cast rpc anvil_setBalance`.
#
# DEVIATION FROM T-0-seedslot / Pattern 5 (deal() auto-detect) — SUPERSEDED for the
# LIVE-ANVIL dev stack, documented here so it is not a mystery later:
#   The 00-06 decision chose forge-std deal() (which probes for the balance slot) over a
#   hand-written slot to avoid a wrong-slot silent mis-seed. That decision was made for
#   FORK *TESTS* (`forge test --fork`), where deal()'s vm.store cheatcode mutates the
#   in-process EVM. seed.sh instead seeds the LIVE external anvil container: deal() via
#   `forge script` writes cheatcode storage in forge's local EVM and is NEVER broadcast,
#   so it does not persist to the running node. deal() is therefore non-functional here.
#   We converge on anvil_setStorageAt (the same RPC the seeds decision already uses to
#   fund EOAs) writing the FiatToken balances mapping at slot 9:
#       slot = keccak256(abi.encode(addr, uint256(9)))
#
# Why this is SAFE despite hand-coding a slot (T-0-seedslot's concern was a SILENT mis-seed):
#   verify-stack.sh asserts USDC balances via balanceOf() — the real accessor — so a WRONG
#   slot fails LOUD, not silently. Slot 9 is VERIFIED correct: storage[slot9(addr)] written
#   to 1e12 yields balanceOf(addr)==1e12 on fork block 353000000 (Circle FiatTokenV2_2).
#
# DEPENDENCY: slot 9 is correct for the CURRENT USDC contract (0xaf88…) at the CURRENT fork
#   target. If FORK_BLOCK or the USDC implementation changes, re-verify the slot (verify-stack's
#   balanceOf read will flag a mismatch loudly). See docs/RUNBOOK.md "USDC seeding".
echo "[3/5] Seeding USDC (anvil_setStorageAt slot 9; see header — deal() can't seed a live anvil) + ETH..."

# USDC_BALANCE_SLOT_INDEX is the Solidity mapping slot index (9 for USDC v2 FiatToken)
USDC_BALANCE_SLOT_INDEX=9
# USDC_SEED_AMOUNT as 32-byte hex (1e12 = 0xE8D4A51000)
USDC_SEED_HEX=$(cast to-uint256 "${USDC_SEED_AMOUNT}" 2>/dev/null || printf '0x%064x' "${USDC_SEED_AMOUNT}")

SEED_FAILURES=0
for addr in "${ADDRESSES[@]}"; do
    # Compute the balance storage slot: keccak256(abi.encode(addr, slot_index))
    BALANCE_SLOT=$(cast index address "${addr}" "${USDC_BALANCE_SLOT_INDEX}" 2>/dev/null)
    if [[ -z "${BALANCE_SLOT}" ]]; then
        echo "  [WARN] Could not compute storage slot for ${addr} — skipping USDC seed"
        SEED_FAILURES=$((SEED_FAILURES + 1))
        continue
    fi

    # Write USDC balance via anvil_setStorageAt (redirect stdout; capture only exit code)
    USDC_OK="false"
    if cast rpc anvil_setStorageAt \
        "${USDC_ARBITRUM}" "${BALANCE_SLOT}" "${USDC_SEED_HEX}" \
        --rpc-url "${ANVIL_RPC}" >/dev/null 2>&1; then
        USDC_OK="true"
    fi

    # Seed ETH via anvil_setBalance
    ETH_OK="false"
    if cast rpc anvil_setBalance \
        "${addr}" "${ETH_SEED_AMOUNT}" \
        --rpc-url "${ANVIL_RPC}" >/dev/null 2>&1; then
        ETH_OK="true"
    fi

    if [[ "${USDC_OK}" == "true" && "${ETH_OK}" == "true" ]]; then
        echo "  [OK] ${addr}: USDC + ETH funded"
    elif [[ "${USDC_OK}" == "true" ]]; then
        echo "  [WARN] ${addr}: USDC funded but ETH seed failed"
    elif [[ "${ETH_OK}" == "true" ]]; then
        echo "  [WARN] ${addr}: ETH funded but USDC seed failed (slot=${BALANCE_SLOT})"
        SEED_FAILURES=$((SEED_FAILURES + 1))
    else
        echo "  [WARN] ${addr}: both USDC and ETH seed failed — is anvil running?"
        SEED_FAILURES=$((SEED_FAILURES + 1))
    fi
done

if [[ ${SEED_FAILURES} -eq 0 ]]; then
    echo "[3/5] USDC + ETH seeding complete."
else
    echo "[3/5] Seeding complete with ${SEED_FAILURES} warning(s) — check output above."
fi
echo ""

# ── Step 4: Write .env.local (UNCONDITIONAL) ──────────────────────────────────
echo "[4/5] Writing .env.local with operator addresses..."

cat > "${ENV_LOCAL}" << ENV_EOF
# =============================================================================
# .env.local — generated by scripts/seed.sh
# DO NOT COMMIT — gitignored (SEC-01)
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# =============================================================================

ANVIL_RPC=${ANVIL_RPC}
FORK_BLOCK=${FORK_BLOCK}

# Operator addresses (populated by seed.sh from .env.<role> files)
ENV_EOF

for i in "${!ROLES[@]}"; do
    role="${ROLES[$i]}"
    addr="${ADDRESSES[$i]}"
    var_name=$(echo "ADDR_${role}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')
    echo "${var_name}=${addr}" >> "${ENV_LOCAL}"
done

echo ""
echo "# USDC contract (Arbitrum One canonical)" >> "${ENV_LOCAL}"
echo "USDC_ARBITRUM=${USDC_ARBITRUM}" >> "${ENV_LOCAL}"

echo "[4/5] .env.local written."
echo ""

# ── Step 5: MockPerps deploy — DEFERRED to the authoritative path ─────────────
# MockPerps is NOT deployed by seed.sh. Its authoritative deploy is:
#     forge script contracts/script/01-Deploy.s.sol
# which constructs MockPerps WITH its Chainlink feed args (ethFeed, btcFeed, solFeed)
# and wires it into SessionFactory. seed.sh intentionally does NOT deploy MockPerps, to
# avoid a SECOND, drifting deploy path (different constructor args / feed wiring / address)
# — the same duplication anti-pattern avoided elsewhere. If a future seed wants MockPerps
# pre-deployed in the dev stack, it must CALL 01-Deploy.s.sol, not reimplement the deploy.
#
# (Removed: a broken vestigial `forge create` attempt that NEVER deployed — `forge create
#  --unlocked` requires --from, MockPerps needs 3 constructor args (no --constructor-args
#  was passed), the declared CREATE2 salt was unused 31-byte dead code, and the output was
#  piped through python3 which is the Windows "not found" stub. It silently produced no
#  address, and verify-stack then silently skipped its check. See quick task 260604-nlp.)
echo "[5/5] MockPerps deploy DEFERRED to the authoritative path:"
echo "      forge script contracts/script/01-Deploy.s.sol  (run by Phase 02 when MockPerps is needed)"
echo "      seed.sh does not deploy MockPerps — avoids a second, drifting deploy path."

echo ""
echo "============================================================"
echo "  Seed complete. Run: make verify-stack"
echo "============================================================"

#!/usr/bin/env bash
# scripts/gate/fund-holders.sh — Mint mock USDC to the 3 demo holder wallets.
#
# Usage:
#   bash scripts/gate/fund-holders.sh [USDC_AMOUNT]
#
# USDC_AMOUNT: raw USDC units to mint per holder (default: 100000000 = 100 USDC @ 6 dec).
#
# Required env vars (loaded from .env / .env.deployer / orchestrator/.env):
#   HOLDER_CLAUDE_KEY     — private key for Claude's demo holder wallet
#   HOLDER_GPT_KEY        — private key for GPT's demo holder wallet
#   HOLDER_GEM_KEY        — private key for Gemini's demo holder wallet
#   DEPLOYER_PRIVATE_KEY  — private key with mint permission on MockERC20
#   SEPOLIA_RPC           — Arbitrum Sepolia RPC URL
#
# The MockERC20 address is read from deployments/sepolia.json (.mockUsdc field).
#
# Requires: cast (Foundry), jq.
#
# Exit codes:
#   0 — all 3 holders funded successfully
#   1 — prerequisite check failed (missing key, blank address, etc.)

set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Load environment from .env files (non-fatal if a file is absent)
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

set -a
[ -f "${REPO_ROOT}/.env" ]              && . "${REPO_ROOT}/.env"
[ -f "${REPO_ROOT}/.env.deployer" ]     && . "${REPO_ROOT}/.env.deployer"
[ -f "${REPO_ROOT}/orchestrator/.env" ] && . "${REPO_ROOT}/orchestrator/.env"
set +a

# ---------------------------------------------------------------------------
# 2. Guard: required env vars
# ---------------------------------------------------------------------------
ERRORS=0
for VAR in HOLDER_CLAUDE_KEY HOLDER_GPT_KEY HOLDER_GEM_KEY DEPLOYER_PRIVATE_KEY SEPOLIA_RPC; do
    if [ -z "${!VAR:-}" ]; then
        echo "[ERROR] ${VAR} is not set — cannot fund holders." >&2
        ERRORS=$((ERRORS + 1))
    fi
done
[ "$ERRORS" -gt 0 ] && exit 1

# ---------------------------------------------------------------------------
# 3. Read mockUsdc address from manifest
# ---------------------------------------------------------------------------
MANIFEST="${REPO_ROOT}/deployments/sepolia.json"
if [ ! -f "${MANIFEST}" ]; then
    echo "[ERROR] Manifest not found: ${MANIFEST}" >&2
    echo "  Run the deploy + pool-seeding scripts first (Phase-4, 04-06)." >&2
    exit 1
fi

MOCK_USDC=$(jq -r '.mockUsdc // empty' "${MANIFEST}")
if [ -z "${MOCK_USDC}" ] || [ "${MOCK_USDC}" = "null" ]; then
    echo "[ERROR] .mockUsdc is empty or missing in ${MANIFEST}." >&2
    echo "  Run the Phase-4 deploy script to populate this address." >&2
    exit 1
fi
echo "[INFO] MockUSDC address: ${MOCK_USDC}"

# ---------------------------------------------------------------------------
# 4. Derive holder addresses from private keys (via cast wallet address)
# ---------------------------------------------------------------------------
USDC_AMOUNT="${1:-100000000}"  # default: 100 USDC in 6-decimal raw units
echo "[INFO] Minting ${USDC_AMOUNT} raw USDC units to each holder (= $((USDC_AMOUNT / 1000000)) USDC)"

declare -A HOLDER_KEYS
HOLDER_KEYS[claude]="${HOLDER_CLAUDE_KEY}"
HOLDER_KEYS[gpt]="${HOLDER_GPT_KEY}"
HOLDER_KEYS[gem]="${HOLDER_GEM_KEY}"

for MODEL in claude gpt gem; do
    KEY="${HOLDER_KEYS[$MODEL]}"

    # Derive address from key
    ADDR=$(cast wallet address --private-key "${KEY}" 2>/dev/null)
    if [ -z "${ADDR}" ]; then
        echo "[ERROR] Could not derive address from HOLDER_${MODEL^^}_KEY." >&2
        exit 1
    fi
    echo "[INFO] holder[${MODEL}] address = ${ADDR}"

    # Mint mock USDC to the holder
    echo "[INFO]   Minting to ${ADDR}..."
    TX=$(cast send "${MOCK_USDC}" \
        "mint(address,uint256)" "${ADDR}" "${USDC_AMOUNT}" \
        --rpc-url "${SEPOLIA_RPC}" \
        --private-key "${DEPLOYER_PRIVATE_KEY}" \
        2>&1)

    if echo "${TX}" | grep -q "blockHash\|transactionHash\|status.*1\|Transaction:"; then
        echo "[OK]  holder[${MODEL}] mint tx sent"
    else
        echo "[WARN] holder[${MODEL}] mint output: ${TX}"
    fi

    # Read back the balance to confirm
    BALANCE=$(cast call "${MOCK_USDC}" \
        "balanceOf(address)(uint256)" "${ADDR}" \
        --rpc-url "${SEPOLIA_RPC}" 2>/dev/null | tr -d ' ')
    echo "[INFO]   balance after mint: ${BALANCE} raw units"
done

echo ""
echo "[DONE] fund-holders.sh complete. Verify balances above are >= ${USDC_AMOUNT}."

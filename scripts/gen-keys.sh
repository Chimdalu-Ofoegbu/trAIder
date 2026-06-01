#!/usr/bin/env bash
# =============================================================================
# scripts/gen-keys.sh — Generate four independent EOAs (D-16 / D-17 / SEC-01)
#
# Generates four UNRELATED private keys via `cast wallet new` (NOT BIP-32
# derived — a single mnemonic would compromise all four from one seed).
# Each key is written into its own gitignored .env.<role> file at repo root.
#
# Idempotent: if a .env.<role> file already exists, it is SKIPPED (never
# overwritten) and a warning is printed. Run again only in a fresh checkout or
# after deliberately deleting the file.
#
# Usage:
#   bash scripts/gen-keys.sh
#
# Output:
#   Four addresses printed to stdout for out-of-band backup (password manager).
#   Private keys are written ONLY into the gitignored .env.* files — never
#   displayed beyond initial key generation.
#
# SEC-01: compromise of one key yields no information about the others.
# D-17: BIP-32 derivation (one mnemonic → four keys) explicitly rejected.
# D-67: Mainnet deploy/settlement uses Ledger Nano X; these four keys are for
#        automated runtime signing (testnet + session operation).
# =============================================================================

set -euo pipefail

# Resolve repo root (script may be run from any directory)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Four-key topology (D-16)
declare -a ROLES=("deployer" "operator-trade" "operator-journal" "gas")
declare -a VAR_NAMES=("DEPLOYER_PRIVATE_KEY" "OPERATOR_TRADE_PRIVATE_KEY" "OPERATOR_JOURNAL_PRIVATE_KEY" "GAS_PRIVATE_KEY")
declare -a ADDRESSES=()

echo "============================================================"
echo "  trAIder — Four-key EOA generation (D-17, SEC-01)"
echo "============================================================"
echo ""

for i in "${!ROLES[@]}"; do
    role="${ROLES[$i]}"
    var_name="${VAR_NAMES[$i]}"
    env_file="${REPO_ROOT}/.env.${role}"

    if [[ -f "$env_file" ]]; then
        # Idempotent: skip existing files (never overwrite)
        echo "[SKIP] .env.${role} already exists — skipping (delete manually to regenerate)"
        # Extract address from existing file for display
        existing_addr=$(grep -E "^[A-Z_]+=0x[0-9a-fA-F]{40}$" "$env_file" 2>/dev/null | head -1 | cut -d'=' -f2 || echo "(address not found in file)")
        ADDRESSES+=("$existing_addr (existing)")
        continue
    fi

    # Generate a fresh independent keypair (NOT BIP-32 — each call is unrelated)
    keypair_output=$(cast wallet new 2>&1)

    # Parse address and private key from cast wallet new output
    # Expected output format:
    #   Successfully created new keypair.
    #   Address:     0x...
    #   Private key: 0x...
    address=$(echo "$keypair_output" | grep -E "^Address:" | awk '{print $2}')
    private_key=$(echo "$keypair_output" | grep -E "^Private key:" | awk '{print $3}')

    if [[ -z "$address" || -z "$private_key" ]]; then
        echo "[ERROR] Failed to parse cast wallet new output for role: $role"
        echo "Output was: $keypair_output"
        exit 1
    fi

    # Write the env file (chmod 600 for minimal local exposure)
    cat > "$env_file" <<EOF
# =============================================================================
# .env.${role} — trAIder operator key (${role^^})
# NEVER commit this file. It is gitignored.
# Back up out-of-band (password manager) immediately.
#
# D-16: per-key .env file; only the service that needs this key loads it.
# D-17: generated via cast wallet new (independent EOA, not BIP-32 derived).
# SEC-01: four UNRELATED keys; compromise of one does not compromise others.
# =============================================================================

# Address (safe to display/share): ${address}
${var_name}=${private_key}
EOF
    chmod 600 "$env_file"
    ADDRESSES+=("$address")
    echo "[OK]   Generated .env.${role} — Address: $address"
done

echo ""
echo "============================================================"
echo "  Generated addresses (BACK THESE UP OUT-OF-BAND NOW)"
echo "  Private keys are in the .env.* files — NEVER share them."
echo "============================================================"
echo ""
echo "  Deployer:        ${ADDRESSES[0]}"
echo "  Operator Trade:  ${ADDRESSES[1]}"
echo "  Operator Journal: ${ADDRESSES[2]}"
echo "  Gas:             ${ADDRESSES[3]}"
echo ""
echo "D-67: For mainnet deploy + settlement, use a Ledger Nano X."
echo "      These runtime EOAs are for automated signing only (testnet + session)."
echo ""

# Verify all four are gitignored (should be — .gitignore covers .env.* since Plan 00)
echo "Verifying gitignore coverage..."
all_gitignored=true
for role in "${ROLES[@]}"; do
    env_file="${REPO_ROOT}/.env.${role}"
    if git -C "$REPO_ROOT" check-ignore -q "$env_file" 2>/dev/null; then
        echo "  [OK]   .env.${role} is gitignored"
    else
        echo "  [WARN] .env.${role} is NOT gitignored — check .gitignore immediately!"
        all_gitignored=false
    fi
done

echo ""
if [[ "$all_gitignored" == "true" ]]; then
    echo "[OK] All four .env.* files are gitignored. Keys are safe from accidental commit."
else
    echo "[ERROR] Some .env.* files are not gitignored. Fix .gitignore BEFORE using these keys."
    exit 1
fi

echo ""
echo "Next steps:"
echo "  1. Back up all four private keys to your password manager NOW."
echo "  2. Fund the Gas key with ETH (for keeper gas on Arbitrum Sepolia)."
echo "  3. Fund the Operator Trade key with USDC (session capital)."
echo "  4. Run: bash scripts/test-gitleaks-blocks-secret.sh (SEC-01 smoke test)"
echo "  5. Reference docs/KEY-TOPOLOGY.md for service-to-key mapping."

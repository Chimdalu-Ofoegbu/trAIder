#!/usr/bin/env bash
# =============================================================================
# scripts/test-gitleaks-blocks-secret.sh — SEC-01 smoke test
#
# Verifies that gitleaks correctly BLOCKS a planted fake secret before it
# could be committed. This is the authoritative smoke test for the secret-
# scanning layer (T-0-secret threat mitigation).
#
# Exit code:
#   0 — gitleaks correctly blocked the planted secret (security is working)
#   1 — gitleaks did NOT block the planted secret (security is broken — ALERT)
#
# Usage:
#   bash scripts/test-gitleaks-blocks-secret.sh
#
# How it works:
#   1. Writes a FAKE private key (not a real secret) into a temp file
#   2. Runs `gitleaks detect` against that file (mimics what the pre-commit
#      hook and CI job do before any code is merged)
#   3. Asserts gitleaks exits NON-ZERO (i.e., it detected the secret)
#   4. Cleans up the temp file
#   5. Exits 0 only if gitleaks correctly detected and blocked the secret
#
# SEC-01: This test proves that the gitleaks layer (D-70) is operational.
# T-0-secret: Validates the "gitleaks pre-commit + CI blocks committed secrets"
#              mitigation from the threat register.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_FILE=""

cleanup() {
    if [[ -n "$TEMP_FILE" && -f "$TEMP_FILE" ]]; then
        rm -f "$TEMP_FILE"
    fi
}
trap cleanup EXIT

echo "============================================================"
echo "  trAIder — SEC-01 gitleaks smoke test"
echo "============================================================"
echo ""
echo "Testing: gitleaks correctly blocks a planted fake private key"
echo ""

# ---- Step 1: Create a temp file with a planted fake secret ----
# This is a FAKE/NON-REAL private key for test purposes only.
# Format: 0x + 64 hex chars — matches the "generic private key" pattern
# that gitleaks detects as a high-confidence secret.
TEMP_FILE="${REPO_ROOT}/.secret-smoke-test-$(date +%s).tmp"

cat > "$TEMP_FILE" <<'FAKE_SECRET'
# This file is a gitleaks smoke test fixture — NOT a real secret.
# The key below is FAKE and was never used for any wallet.
FAKE_PRIVATE_KEY=0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
FAKE_AWS_KEY=AKIAIOSFODNN7EXAMPLE1234
FAKE_ETH_KEY=0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
FAKE_SECRET
echo "[INFO] Planted fake secrets in: $TEMP_FILE"

# ---- Step 2: Run gitleaks detect against the temp file ----
# gitleaks detect scans the file for secret patterns.
# We expect it to exit NON-ZERO (secrets found = detection working).
echo "[RUN]  gitleaks detect --no-git --config .gitleaks.toml --source $TEMP_FILE"
echo ""

# Use --no-git to scan file directly (not via git history)
# Use --redact to avoid printing actual secret values in test output
# Use --config to apply the project-specific rules (Ethereum keys, LLM API keys)
if gitleaks detect \
    --no-git \
    --config "${REPO_ROOT}/.gitleaks.toml" \
    --source "$TEMP_FILE" \
    --redact \
    2>&1; then
    # gitleaks exited 0 = it did NOT detect the secret = SECURITY FAILURE
    echo ""
    echo "============================================================"
    echo "  [FAIL] gitleaks did NOT detect the planted secret!"
    echo ""
    echo "  This means secret scanning is NOT working correctly."
    echo "  Real private keys could be committed without detection."
    echo ""
    echo "  Action required:"
    echo "  1. Check gitleaks installation: gitleaks version"
    echo "  2. Check .gitleaks.toml allowlist rules"
    echo "  3. Verify gitleaks rules cover 0x-prefixed hex private keys"
    echo "============================================================"
    exit 1
else
    GITLEAKS_EXIT=$?
    # gitleaks exited non-zero = it detected the secret = SECURITY WORKING
    echo ""
    echo "============================================================"
    echo "  [PASS] gitleaks correctly blocked the planted secret!"
    echo ""
    echo "  Exit code: $GITLEAKS_EXIT (non-zero = detection working)"
    echo "  Secret scanning is operational (D-70, T-0-secret mitigated)."
    echo "============================================================"
    # Cleanup happens via trap
    exit 0
fi

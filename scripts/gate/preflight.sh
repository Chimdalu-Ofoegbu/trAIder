#!/usr/bin/env bash
# scripts/gate/preflight.sh — Thin wrapper around gate.preflight Python module.
#
# Usage:
#   bash scripts/gate/preflight.sh [OPTIONS]
#
# Loads environment from .env / .env.deployer / orchestrator/.env, then delegates
# to `uv run --project orchestrator python -m gate.preflight`.
#
# Pass any additional flags (e.g. --manifest /path/to/sepolia.json) as positional
# arguments; they are forwarded to gate.preflight unchanged.
#
# Required env vars (loaded from .env files above):
#   SEPOLIA_RPC            — Arbitrum Sepolia RPC URL
#   ARB_KEY4_PRIVATE_KEY   — Private key for arb-bot key #4 (ETH balance check)
#   OPERATOR_LP_KEY_PRIVATE_KEY — Private key for LP operator (ETH balance check)
#
# Exit codes mirror gate.preflight:
#   0 — all preflight checks passed (ALL CHECKS PASSED — gate run is ready to launch)
#   1 — one or more checks failed (details printed to stdout)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

set -a
[ -f "${REPO_ROOT}/.env" ]              && . "${REPO_ROOT}/.env"
[ -f "${REPO_ROOT}/.env.deployer" ]     && . "${REPO_ROOT}/.env.deployer"
[ -f "${REPO_ROOT}/orchestrator/.env" ] && . "${REPO_ROOT}/orchestrator/.env"
set +a

exec uv run \
    --env-file "${REPO_ROOT}/orchestrator/.env" \
    --project "${REPO_ROOT}/orchestrator" \
    python -m gate.preflight "$@"

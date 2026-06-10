#!/usr/bin/env bash
# scripts/gate/run-gate.sh — Thin wrapper around gate.run_gate Python module.
#
# Usage:
#   bash scripts/gate/run-gate.sh [--full-run|--dry-run] [OPTIONS]
#
# Loads environment from .env / .env.deployer / orchestrator/.env, exports gate
# timing env vars (GATE_DURATION, FIRE_THRESHOLD_BPS), then delegates to
# `uv run --project orchestrator python -m gate.run_gate`.
#
# Pass any additional flags (e.g. --step-through --gate-duration 2700) as positional
# arguments; they are forwarded to gate.run_gate unchanged.
#
# Environment overrides (all have sensible defaults):
#   GATE_DURATION          — Gate session duration in seconds (default: 3600 = 1h)
#   FIRE_THRESHOLD_BPS     — Arb-bot hysteresis in bps (default: 250 = 2.5%)
#
# Typical gate run sequence:
#   bash scripts/gate/fund-holders.sh
#   bash scripts/gate/preflight.sh
#   bash scripts/gate/run-gate.sh --full-run
#   # For step-through (interactive narration):
#   bash scripts/gate/run-gate.sh --full-run --step-through
#   # For a shorter 45-min run:
#   GATE_DURATION=2700 bash scripts/gate/run-gate.sh --full-run
#
# Exit codes mirror gate.run_gate:
#   0 — all 7 D-16 HARD CRITERIA passed
#   1 — manifest/config error
#   2 — GATE FAIL (one or more hard criteria failed)
#   3 — unexpected error (see logs)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

set -a
[ -f "${REPO_ROOT}/.env" ]              && . "${REPO_ROOT}/.env"
[ -f "${REPO_ROOT}/.env.deployer" ]     && . "${REPO_ROOT}/.env.deployer"
[ -f "${REPO_ROOT}/orchestrator/.env" ] && . "${REPO_ROOT}/orchestrator/.env"
set +a

# Gate timing defaults (env-overridable before calling this script)
export GATE_DURATION="${GATE_DURATION:-3600}"
export FIRE_THRESHOLD_BPS="${FIRE_THRESHOLD_BPS:-250}"

echo "[run-gate.sh] GATE_DURATION=${GATE_DURATION}s  FIRE_THRESHOLD_BPS=${FIRE_THRESHOLD_BPS}bps"
echo "[run-gate.sh] Delegating to gate.run_gate with args: $*"

exec uv run \
    --env-file "${REPO_ROOT}/orchestrator/.env" \
    --project "${REPO_ROOT}/orchestrator" \
    python -m gate.run_gate "$@"

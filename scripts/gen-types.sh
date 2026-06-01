#!/usr/bin/env bash
# scripts/gen-types.sh — Generate frontend TypeScript types from backend Pydantic models.
#
# Implements `make gen-types` (D-27, IFACE-04).
#
# Pipeline:
#   1. Run backend.openapi_export (Python/uv) -> openapi.json (temp)
#   2. Run openapi-typescript (pnpm exec) -> frontend/types/api.ts
#   3. (--check mode) CI drift gate:
#        a) Stage current/committed file
#        b) Regenerate in-place
#        c) git diff --cached --exit-code (staged baseline vs regenerated)
#        d) Grep for all 7 expected WS type names (Pitfall 3: missing type = no diff)
#
# Usage:
#   bash scripts/gen-types.sh            # generate (writes frontend/types/api.ts)
#   bash scripts/gen-types.sh --check    # CI drift gate (fails on any drift or missing type)
#
# Prerequisites:
#   - uv in PATH (Python / backend package)
#   - pnpm in PATH (frontend devDeps including openapi-typescript@7.13.0)
#   - Run from the repository root (or any directory — REPO_ROOT is computed)
#
# Windows compatibility: This script is POSIX bash. Run via Git Bash or the
# Bash tool. pnpm and uv are both available in Git Bash on this host.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths (all computed relative to script location so they work from any cwd)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"
OUTPUT_TS="${FRONTEND_DIR}/types/api.ts"
OPENAPI_TMP="${REPO_ROOT}/.openapi_export_tmp.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf '[gen-types] %s\n' "$*"; }
error() { printf '[gen-types] ERROR: %s\n' "$*" >&2; }

cleanup() {
    rm -f "${OPENAPI_TMP}"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 1: Export OpenAPI JSON from backend Pydantic models
# ---------------------------------------------------------------------------

info "Exporting OpenAPI JSON from backend.openapi_export..."
cd "${BACKEND_DIR}"
uv run python -m backend.openapi_export --out "${OPENAPI_TMP}"
cd "${REPO_ROOT}"

if [ ! -f "${OPENAPI_TMP}" ]; then
    error "openapi_export did not produce ${OPENAPI_TMP}"
    exit 1
fi

info "OpenAPI JSON exported: ${OPENAPI_TMP}"

# ---------------------------------------------------------------------------
# Step 2: Run openapi-typescript to generate frontend/types/api.ts
# ---------------------------------------------------------------------------

info "Running openapi-typescript -> ${OUTPUT_TS}..."
mkdir -p "${FRONTEND_DIR}/types"

cd "${FRONTEND_DIR}"
# Use pnpm exec to run the locally installed openapi-typescript devDep.
# Fall back to pnpm dlx if the binary is somehow absent (first-run / CI before pnpm install).
if [ -x "node_modules/.bin/openapi-typescript" ]; then
    pnpm exec openapi-typescript "${OPENAPI_TMP}" -o "${OUTPUT_TS}"
else
    info "openapi-typescript not in node_modules; using pnpm dlx fallback..."
    pnpm dlx openapi-typescript@7.13.0 "${OPENAPI_TMP}" -o "${OUTPUT_TS}"
fi
cd "${REPO_ROOT}"

if [ ! -f "${OUTPUT_TS}" ]; then
    error "openapi-typescript did not produce ${OUTPUT_TS}"
    exit 1
fi

info "Generated: ${OUTPUT_TS}"

# ---------------------------------------------------------------------------
# Step 3 (--check mode): CI drift gate
# ---------------------------------------------------------------------------

if [ "${1:-}" = "--check" ]; then
    info "Running CI drift gate (--check mode)..."

    # ---- Pitfall 3 / Warning 3: untracked-file no-op ----
    # A bare `git diff --exit-code` does NOT diff untracked files.
    # If frontend/types/api.ts is brand-new and untracked, the check would silently pass
    # even though the file just changed. We MUST `git add` first so git treats the file
    # as staged (even if it was previously untracked), then compare cached vs working tree.
    #
    # Protocol:
    #   (a) Stage the current/committed (or newly generated) file as the baseline.
    #   (b) Regenerate into the same path (already done in Steps 1+2 above).
    #   (c) Stage the regenerated version.
    #   (d) git diff --cached --exit-code -> fails if any content changed.
    #       Works for both: first-run untracked AND already-committed states.
    #   (e) Grep for all 7 expected WS type names: missing type => fail.
    #       (A type removed from Pydantic produces no diff but silently breaks the frontend.)

    info "Staging generated file as baseline for diff..."
    git add "${OUTPUT_TS}"

    # The file was just regenerated (Steps 1+2), so re-stage to capture any content change
    git add "${OUTPUT_TS}"

    info "Checking for drift with git diff --cached --exit-code..."
    if ! git diff --cached --exit-code "${OUTPUT_TS}"; then
        error "Drift detected: frontend/types/api.ts differs from the committed/staged baseline."
        error "Run 'bash scripts/gen-types.sh' to regenerate, then commit the updated file."
        exit 1
    fi

    info "No content drift detected."

    # ---- Pitfall 3: type-presence check ----
    # A WS model removed from the Python source produces no diff in the already-generated file,
    # but the absence of NavTick / TradeEvent / etc. in api.ts silently breaks Zustand reducers.
    # Grep for each expected name to catch this case.

    info "Verifying all expected WS type names are present in ${OUTPUT_TS}..."
    EXPECTED_TYPES=(
        "NavTick"
        "TradeEvent"
        "JournalEvent"
        "ModelStatus"
        "ArbOpp"
        "SessionEvent"
        "CurrentState"
    )
    MISSING=()
    for type_name in "${EXPECTED_TYPES[@]}"; do
        if ! grep -q "${type_name}" "${OUTPUT_TS}"; then
            MISSING+=("${type_name}")
        fi
    done

    if [ ${#MISSING[@]} -gt 0 ]; then
        error "Missing WS type names in ${OUTPUT_TS}: ${MISSING[*]}"
        error "This means openapi-typescript did not pick up these models."
        error "Check backend/src/backend/openapi_export.py — all 7 models must be in the Union."
        exit 1
    fi

    info "All expected WS type names present: ${EXPECTED_TYPES[*]}"
    info "Drift gate PASSED."
else
    # Non-check mode: just verify presence (not a CI gate, but helpful locally)
    info "Verifying WS type names present in generated file..."
    for type_name in NavTick TradeEvent JournalEvent ModelStatus ArbOpp SessionEvent CurrentState; do
        if ! grep -q "${type_name}" "${OUTPUT_TS}"; then
            error "WARNING: expected type '${type_name}' not found in ${OUTPUT_TS}"
            error "Check backend/src/backend/openapi_export.py"
            exit 1
        fi
    done
    info "All expected WS type names present."
    info "Done. Generated: ${OUTPUT_TS}"
fi

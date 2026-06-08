"""
orchestrator.tests.integration.test_post_deploy_verification — Pytest wrapper
for the post-deploy Sepolia integration verification harness (03-08 Step 4).

Exercises every cell of 03-INTEGRATION-MATRIX.md against the REAL deployed
Sepolia contracts via direct web3 read calls. NO Opus, NO agent loop, NO gate
budget, NO DB required.

Skips cleanly (EXPLICIT-DEFER) when SEPOLIA_RPC or deployments/sepolia.json
are absent. Asserts all-green when both are present.

This test is designed to be run after a (re)deploy to confirm:
  - Every AUTH cell: ownership/authorization wiring is correct in the
    post-ownership-transfer state
  - Every STATE cell: bytecode present, vault active, adapter wired
  - Every TIMING cell: executionDelay=3, MAX_STALENESS=3600s, thresholds correct

CANARY CELL:
  test_auth_authorized_publishers — tests journal.authorizedPublishers(operator-journal EOA)
  This cell is the GAP #5 canary:
    - FAILS on the current (pre-redeploy) deployment — expected, pre-fix state
    - MUST PASS after the redeploy that fixes GAP #5

Run directly (no make, Windows-friendly):
  cd <repo-root>
  source .env 2>/dev/null
  uv run --project orchestrator pytest orchestrator/tests/integration/test_post_deploy_verification.py -v

Or with an explicit RPC:
  SEPOLIA_RPC=https://... uv run --project orchestrator pytest \\
      orchestrator/tests/integration/test_post_deploy_verification.py -v

References:
  03-INTEGRATION-MATRIX.md — matrix cell definitions + gap list
  deployments/sepolia.json — deployed addresses
  orchestrator/src/orchestrator/verify_integration.py — core harness logic
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_MANIFEST_PATH = _REPO_ROOT / "deployments" / "sepolia.json"

# ---------------------------------------------------------------------------
# Skip guard: all tests in this module skip when creds are absent
# ---------------------------------------------------------------------------

_SKIP_REASON = (
    "EXPLICIT-DEFER: SEPOLIA_RPC not set or deployments/sepolia.json missing. "
    "Set SEPOLIA_RPC and ensure deployments/sepolia.json exists to run the "
    "post-deploy integration verification harness."
)


def _sepolia_configured() -> bool:
    """Return True if SEPOLIA_RPC is set and the manifest exists."""
    rpc = os.environ.get("SEPOLIA_RPC") or os.environ.get("ARB_SEPOLIA_RPC")
    return bool(rpc) and _MANIFEST_PATH.exists()


# ---------------------------------------------------------------------------
# Module-level fixture: shared verification report
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def verification_report():
    """
    Run the full verification harness once per test module and cache the report.

    This fixture is expensive (many RPC calls); running it once and sharing
    results across all assertion tests avoids redundant network calls.

    Skips cleanly when SEPOLIA_RPC / manifest are absent.
    """
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)

    rpc_url = (
        os.environ.get("SEPOLIA_RPC")
        or os.environ.get("ARB_SEPOLIA_RPC")
        or "https://sepolia-rollup.arbitrum.io/rpc"
    )

    # Import here (not at module level) so missing web3 doesn't break collection
    try:
        from orchestrator.verify_integration import (
            _load_eoa_addresses,
            _load_manifest,
            run_verification,
        )
    except ImportError as exc:
        pytest.skip(f"orchestrator.verify_integration import failed: {exc}")

    manifest = _load_manifest()
    if not manifest:
        pytest.skip(f"Manifest not found: {_MANIFEST_PATH}")

    eoas = _load_eoa_addresses()
    report = run_verification(rpc_url, manifest, eoas)
    return report


@pytest.fixture(scope="module")
def result_by_id(verification_report):
    """Map from cell_id to CellResult for easy per-cell lookup."""
    return {r.cell_id: r for r in verification_report.results}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _assert_cell(result_by_id, cell_id: str) -> None:
    """Assert a specific cell passed; fail with expected vs actual if not."""
    cell = result_by_id.get(cell_id)
    if cell is None:
        pytest.fail(
            f"Cell {cell_id!r} not found in verification report. "
            f"Available: {list(result_by_id.keys())}"
        )
    assert cell.passed, (
        f"Cell {cell_id} FAILED: {cell.description}\n"
        f"  expected: {cell.expected}\n"
        f"  actual:   {cell.actual}\n"
        f"  note:     {cell.note}"
    )


# ---------------------------------------------------------------------------
# AUTH tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_auth_journal_owner_is_factory(result_by_id):
    """AUTH-1: journal.owner() must equal sessionFactory after ownership transfer."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "AUTH-1")


@pytest.mark.integration
def test_auth_operator_journal_key(result_by_id):
    """AUTH-2: journal.OPERATOR_JOURNAL_KEY must equal the operator-journal EOA."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "AUTH-2")


@pytest.mark.integration
def test_auth_authorized_publishers(result_by_id):
    """
    AUTH-3: journal.authorizedPublishers(operator-journal EOA) must be true.

    GAP #5 CANARY — this is the primary test for the redeploy fix:
      - EXPECTED TO FAIL on the pre-redeploy deployment (authorizedPublishers
        either missing from ABI or returns false). This is not a bug in the test.
      - MUST PASS after the redeploy that adds authorizedPublishers support to
        JournalRegistry and calls setAuthorizedPublisher(operator_journal_eoa, true)
        in the deploy script before transferOwnership to the factory.

    If this test FAILs after a redeploy intended to fix GAP #5, investigate:
      1. Was setAuthorizedPublisher called before transferOwnership?
      2. Does the new JournalRegistry have the authorizedPublishers mapping?
      3. Does the deploy script authorize the correct operator-journal address?
    """
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "AUTH-3")


@pytest.mark.integration
def test_auth_authorized_vaults_claude(result_by_id):
    """AUTH-4: journal.authorizedVaults(vaultClaude) must be true (set by createSession)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "AUTH-4")


@pytest.mark.integration
def test_auth_vault_orchestrator(result_by_id):
    """AUTH-5: vault.orchestrator() must equal the operator-trade EOA (onlyOrchestrator gate)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "AUTH-5")


@pytest.mark.integration
def test_auth_vault_operator(result_by_id):
    """AUTH-6: vault.operator() must equal the deployer EOA (VAULT-08: operator no-withdraw)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "AUTH-6")


@pytest.mark.integration
def test_auth_vault_adapter_nonzero(result_by_id):
    """AUTH-7: vault.adapter() must equal manifest.mockPerps (non-zero address)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "AUTH-7")


# ---------------------------------------------------------------------------
# STATE tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_state_bytecode_all_contracts(result_by_id):
    """STATE-1: every manifest address (factory, oracle, journal, vaults, perps, usdc, feeds) has bytecode."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-1")


@pytest.mark.integration
def test_state_vault_asset_is_usdc(result_by_id):
    """STATE-2: vault.asset() must equal mockUsdc."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-2")


@pytest.mark.integration
def test_state_vault_session_active(result_by_id):
    """STATE-3: vault.sessionActive() must be true (live session)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-3")


@pytest.mark.integration
def test_state_vault_session_not_ended(result_by_id):
    """STATE-4: vault.sessionEnded() must be false (session not yet wound down)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-4")


@pytest.mark.integration
def test_state_adapter_wiring(result_by_id):
    """STATE-5: vault.adapter() must equal manifest.mockPerps (adapter gap resolution)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-5")


@pytest.mark.integration
def test_state_eth_feed_fresh(result_by_id):
    """STATE-6: ETH/USD feed has non-zero answer and age < MAX_STALENESS_SEP (21600s)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-6")


@pytest.mark.integration
def test_state_btc_feed_fresh(result_by_id):
    """STATE-7: BTC/USD feed has non-zero answer and age < MAX_STALENESS_SEP (21600s)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-7")


@pytest.mark.integration
def test_state_sol_feed_fresh(result_by_id):
    """STATE-8: SOL/USD feed has non-zero answer and age < MAX_STALENESS_SEP (21600s)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-8")


@pytest.mark.integration
def test_state_settlement_readable(result_by_id):
    """STATE-9: settlement.deadline() > 0 and settlement.settled() == false (not yet finalized)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "STATE-9")


# ---------------------------------------------------------------------------
# TIMING / CONFIG tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_timing_execution_delay(result_by_id):
    """TIMING-1: mockPerps.executionDelay() == 3 (~36-60s on Sepolia, the assumed async window)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "TIMING-1")


@pytest.mark.integration
def test_timing_max_staleness_mockperps(result_by_id):
    """
    TIMING-2: mockPerps.MAX_STALENESS == 3600s AND orch pre-trade threshold < it.

    GAP #1/#7 documentation:
    The binding staleness window for ALL trade submissions (vault.openLong/openShort/closePosition)
    is MockPerps.MAX_STALENESS=3600s, NOT vault.MAX_STALENESS_SEP=21600s. The vault's
    staleness check passes at up to 6h, but MockPerps._markPrice reverts at >3600s.
    The orchestrator pre-trade threshold (3000s) must remain below 3600s.
    """
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "TIMING-2")


@pytest.mark.integration
def test_timing_use_sepolia_staleness(result_by_id):
    """TIMING-3: vault.useSepoliaStaleness == true (6h staleness window active on Sepolia)."""
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)
    _assert_cell(result_by_id, "TIMING-3")


# ---------------------------------------------------------------------------
# Overall all-green gate (run standalone, fail fast if ANY cell fails)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_all_cells_pass(verification_report):
    """
    Master gate: asserts the verification report is all-green.

    This test surfaces a single easy-to-see FAIL if any matrix cell fails.
    Individual per-cell tests above give granular diagnostics.
    """
    if not _sepolia_configured():
        pytest.skip(_SKIP_REASON)

    failed = verification_report.failed
    if failed:
        lines: list[str] = []
        for r in failed:
            lines.append(f"  {r.cell_id}: {r.description}")
            lines.append(f"    expected: {r.expected}")
            lines.append(f"    actual:   {r.actual}")
            if r.note:
                lines.append(f"    note:     {r.note}")
        pytest.fail(f"{len(failed)} cell(s) FAILED:\n" + "\n".join(lines))

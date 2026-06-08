"""
orchestrator.verify_integration — Post-deploy Sepolia integration verification harness.

Exercises EVERY cell of 03-INTEGRATION-MATRIX.md against real deployed Sepolia contracts
via direct web3 read calls (NO Opus, NO agent loop, NO gate budget). One check per matrix
cell. Asserts the post-ownership-transfer AUTH state, every on-chain STATE precondition,
and TIMING/config assumptions.

Exit codes:
  0 — all cells PASS
  1 — one or more cells FAIL (details printed to stdout)

Security: NEVER prints private key values. Reads EOA addresses only.

Usage (standalone, no Postgres/Redis required):

  # From repo root (reads .env for SEPOLIA_RPC):
  source .env 2>/dev/null; uv run --project orchestrator python -m orchestrator.verify_integration

  # Or with explicit RPC:
  SEPOLIA_RPC=https://sepolia-rollup.arbitrum.io/rpc \\
      uv run --project orchestrator python -m orchestrator.verify_integration

  # Passing key-file addresses via env (see .env.operator-* files):
  OPERATOR_JOURNAL_KEY_ADDR=0xD5ee... SEPOLIA_RPC=... \\
      uv run --project orchestrator python -m orchestrator.verify_integration

References:
  03-INTEGRATION-MATRIX.md (every matrix cell)
  deployments/sepolia.json (canonical addresses)
  .env.deployer / .env.operator-trade / .env.operator-journal (EOA addresses)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# File: orchestrator/src/orchestrator/verify_integration.py
# Levels to repo root: orchestrator/ -> src/ -> orchestrator (pkg) -> this file
# = 3 parent traversals from __file__ to reach trAIder/ repo root.
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_MANIFEST_PATH = _REPO_ROOT / "deployments" / "sepolia.json"
_CONTRACTS_OUT = _REPO_ROOT / "contracts" / "out"

# ---------------------------------------------------------------------------
# Threshold constants (from matrix / contracts)
# ---------------------------------------------------------------------------

# The binding staleness window for trading: MockPerps.MAX_STALENESS = 3600s (1h)
MOCK_PERPS_MAX_STALENESS = 3600

# Vault's Sepolia staleness window: MAX_STALENESS_SEP = 21600s (6h)
VAULT_MAX_STALENESS_SEP = 21_600

# Pre-trade warning threshold (orchestrator mitigation for GAP #1/#7)
ORCH_STALENESS_WARN_THRESHOLD = 3000

# Expected executionDelay on Sepolia deployments
EXPECTED_EXECUTION_DELAY = 3

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class CellResult:
    """Result of a single matrix cell check."""

    cell_id: str  # e.g. "AUTH-1", "STATE-3", "TIMING-1"
    description: str  # human-readable description
    passed: bool
    expected: str = ""
    actual: str = ""
    note: str = ""  # extra context (e.g. "needs redeploy", "GAP #5 canary")


@dataclass
class VerificationReport:
    """Aggregated results from all matrix cell checks."""

    results: list[CellResult] = field(default_factory=list)

    def add(self, result: CellResult) -> None:
        self.results.append(result)
        status = "PASS" if result.passed else "FAIL"
        note_suffix = f" [{result.note}]" if result.note else ""
        if result.passed:
            print(f"  [{status}] {result.cell_id}: {result.description}{note_suffix}")
        else:
            print(f"  [{status}] {result.cell_id}: {result.description}{note_suffix}")
            print(f"         expected: {result.expected}")
            print(f"         actual:   {result.actual}")

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed(self) -> list[CellResult]:
        return [r for r in self.results if not r.passed]

    def print_summary(self) -> None:
        """Print PASS/FAIL table to stdout."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        print()
        print("=" * 78)
        print("VERIFICATION SUMMARY")
        print("=" * 78)
        print(f"{'Cell ID':<14} {'Status':<6} {'Description'}")
        print("-" * 78)
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            note = f" [{r.note}]" if r.note else ""
            print(f"{r.cell_id:<14} {status:<6} {r.description}{note}")
        print("-" * 78)
        print(f"Total: {total}  Passed: {passed}  Failed: {failed}")
        print("=" * 78)

        if failed > 0:
            print()
            print("FAILED CELLS (expected vs actual):")
            for r in self.failed:
                print(f"  {r.cell_id}: {r.description}")
                print(f"    expected: {r.expected}")
                print(f"    actual:   {r.actual}")
                if r.note:
                    print(f"    note:     {r.note}")


# ---------------------------------------------------------------------------
# ABI loading helpers
# ---------------------------------------------------------------------------


def _load_abi(contract_name: str, sol_name: str | None = None) -> list:
    """Load ABI from Foundry JSON artifact. Returns [] if artifact missing (graceful)."""
    sol_file = sol_name or f"{contract_name}.sol"
    path = _CONTRACTS_OUT / sol_file / f"{contract_name}.json"
    if not path.exists():
        logger.warning("Artifact not found: %s (run forge build)", path)
        return []
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)["abi"]
    except Exception as exc:
        logger.warning("Failed to load ABI from %s: %s", path, exc)
        return []


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_manifest() -> dict[str, str]:
    """Load deployments/sepolia.json. Returns {} if missing."""
    if not _MANIFEST_PATH.exists():
        return {}
    try:
        with _MANIFEST_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to load manifest %s: %s", _MANIFEST_PATH, exc)
        return {}


# ---------------------------------------------------------------------------
# EOA address loading (from env — NEVER prints private keys)
# ---------------------------------------------------------------------------


def _load_eoa_addresses() -> dict[str, str | None]:
    """
    Load operator EOA addresses from environment variables.
    These should be pre-populated before calling verify_integration.

    Expected env vars (addresses only, NOT private keys):
      DEPLOYER_ADDRESS            Deployer/operator EOA (0xA7d4...)
      OPERATOR_TRADE_ADDRESS      Operator-trade EOA (0x65A4...)
      OPERATOR_JOURNAL_KEY_ADDR   Operator-journal EOA (0xD5ee...)

    Fallback: if the env vars are not set, use the known addresses from the
    03-INTEGRATION-MATRIX.md snapshot (these are public addresses, not secrets).
    """
    # Matrix-confirmed EOA addresses (from 03-INTEGRATION-MATRIX.md, public)
    _KNOWN_DEPLOYER = "0xA7d4CDE3b1540e954dBd3A0cE43feb45B13558fa"
    _KNOWN_OPERATOR_TRADE = "0x65A4e4DDc9Fe83A2c715959c8EaE6b0645824c4A"
    _KNOWN_OPERATOR_JOURNAL = "0xD5ee31fB20C7b37D712f9842536892D9D32a5381"

    return {
        "deployer": os.environ.get("DEPLOYER_ADDRESS", _KNOWN_DEPLOYER),
        "operator_trade": os.environ.get("OPERATOR_TRADE_ADDRESS", _KNOWN_OPERATOR_TRADE),
        "operator_journal": os.environ.get("OPERATOR_JOURNAL_KEY_ADDR", _KNOWN_OPERATOR_JOURNAL),
    }


# ---------------------------------------------------------------------------
# Low-level call helpers
# ---------------------------------------------------------------------------


def _call(web3: Any, contract: Any, fn_name: str, *args: Any) -> Any:
    """Call a contract view function. Returns the raw value or raises."""
    fn = contract.functions[fn_name]
    if args:
        return fn(*args).call()
    return fn().call()


def _has_function(contract: Any, fn_name: str) -> bool:
    """Check if a contract ABI includes a given function."""
    try:
        return fn_name in [fn["name"] for fn in contract.abi if fn.get("type") == "function"]
    except Exception:
        return False


def _get_bytecode(web3: Any, address: str) -> bytes:
    """Get bytecode for an address. Returns b'' if not deployed."""
    try:
        from web3 import Web3

        code = web3.eth.get_code(Web3.to_checksum_address(address))
        return bytes(code)
    except Exception:
        return b""


def _to_checksum(web3: Any, address: str | None) -> str:
    """Return checksummed address or '0x0000...0000'."""
    if not address:
        return "0x" + "0" * 40
    try:
        from web3 import Web3

        return Web3.to_checksum_address(address)
    except Exception:
        return str(address)


# ---------------------------------------------------------------------------
# Main verification routine
# ---------------------------------------------------------------------------


def run_verification(
    rpc_url: str,
    manifest: dict[str, str],
    eoas: dict[str, str | None],
) -> VerificationReport:
    """
    Execute all matrix cell checks against the deployed Sepolia contracts.

    Args:
        rpc_url:  Arbitrum Sepolia RPC URL.
        manifest: Parsed deployments/sepolia.json.
        eoas:     EOA address dict (deployer, operator_trade, operator_journal).

    Returns:
        VerificationReport with one CellResult per matrix cell.
    """
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware

    report = VerificationReport()

    # Connect (sync Web3 — this is a CLI scanner, not an async loop)
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    if not w3.is_connected():
        print(f"ERROR: Cannot connect to RPC at {rpc_url}")
        sys.exit(2)

    block = w3.eth.get_block("latest")
    block_ts = int(block.timestamp)
    print(f"Connected to {rpc_url}")
    print(
        f"Block: {block.number}  Timestamp: {block_ts}  "
        f"({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(block_ts))})"
    )
    print()

    # ── Address extraction ────────────────────────────────────────────────────
    def cs(addr: str | None) -> str:
        return _to_checksum(w3, addr)

    # From manifest
    factory_addr = cs(manifest.get("sessionFactory"))
    oracle_addr = cs(manifest.get("oracle"))
    journal_addr = cs(manifest.get("journal"))
    vault_claude_addr = cs(manifest.get("vaultClaude"))
    vault_gpt_addr = cs(manifest.get("vaultGpt"))
    vault_gem_addr = cs(manifest.get("vaultGem"))
    mock_perps_addr = cs(manifest.get("mockPerps"))
    mock_usdc_addr = cs(manifest.get("mockUsdc"))
    eth_feed_addr = cs(manifest.get("ethFeed"))
    btc_feed_addr = cs(manifest.get("btcFeed"))
    sol_feed_addr = cs(manifest.get("solFeed"))
    sequencer_feed_addr = cs(manifest.get("sequencerFeed"))
    # Settlement is not in the manifest; derive from vault.settlement() below.

    # EOA addresses (addresses only, never private keys)
    deployer_addr = cs(eoas.get("deployer"))
    operator_trade_addr = cs(eoas.get("operator_trade"))
    operator_journal_addr = cs(eoas.get("operator_journal"))

    # ── Load ABIs ─────────────────────────────────────────────────────────────
    journal_abi = _load_abi("JournalRegistry")
    vault_abi = _load_abi("MTokenVault", "mTokenVault.sol")
    mock_perps_abi = _load_abi("MockPerps")
    settlement_abi = _load_abi("SettlementContract")
    chainlink_abi = [
        {
            "inputs": [],
            "name": "latestRoundData",
            "outputs": [
                {"name": "roundId", "type": "uint80"},
                {"name": "answer", "type": "int256"},
                {"name": "startedAt", "type": "uint256"},
                {"name": "updatedAt", "type": "uint256"},
                {"name": "answeredInRound", "type": "uint80"},
            ],
            "stateMutability": "view",
            "type": "function",
        }
    ]

    # Contract instances
    journal = w3.eth.contract(address=journal_addr, abi=journal_abi)
    vault = w3.eth.contract(address=vault_claude_addr, abi=vault_abi)
    mock_perps = w3.eth.contract(address=mock_perps_addr, abi=mock_perps_abi)
    eth_feed = w3.eth.contract(address=eth_feed_addr, abi=chainlink_abi)
    btc_feed = w3.eth.contract(address=btc_feed_addr, abi=chainlink_abi)
    sol_feed = w3.eth.contract(address=sol_feed_addr, abi=chainlink_abi)

    # ── Helper: safe call with error capture ─────────────────────────────────
    _NO_VALUE = object()

    def safe_call(contract: Any, fn_name: str, *args: Any) -> tuple[Any, str | None]:
        """Returns (value, None) on success or (NO_VALUE, error_str) on failure."""
        try:
            val = _call(w3, contract, fn_name, *args)
            return val, None
        except Exception as exc:
            return _NO_VALUE, str(exc)

    print("=" * 78)
    print("AUTH CELLS")
    print("=" * 78)

    # ── AUTH-1: journal.owner() == sessionFactory ─────────────────────────────
    val, err = safe_call(journal, "owner")
    if err:
        report.add(
            CellResult(
                "AUTH-1",
                "journal.owner() == sessionFactory",
                passed=False,
                expected=factory_addr,
                actual=f"ERROR: {err}",
            )
        )
    else:
        actual = cs(val)
        report.add(
            CellResult(
                "AUTH-1",
                "journal.owner() == sessionFactory",
                passed=(actual.lower() == factory_addr.lower()),
                expected=factory_addr,
                actual=actual,
            )
        )

    # ── AUTH-2: journal.OPERATOR_JOURNAL_KEY == operator-journal EOA ─────────
    val, err = safe_call(journal, "OPERATOR_JOURNAL_KEY")
    if err:
        report.add(
            CellResult(
                "AUTH-2",
                "journal.OPERATOR_JOURNAL_KEY == operator-journal EOA",
                passed=False,
                expected=operator_journal_addr,
                actual=f"ERROR: {err}",
            )
        )
    else:
        actual = cs(val)
        report.add(
            CellResult(
                "AUTH-2",
                "journal.OPERATOR_JOURNAL_KEY == operator-journal EOA",
                passed=(actual.lower() == operator_journal_addr.lower()),
                expected=operator_journal_addr,
                actual=actual,
            )
        )

    # ── AUTH-3: journal.authorizedPublishers(operator-journal EOA) == true ────
    # CANARY CELL for GAP #5: WILL FAIL on pre-redeploy deployment.
    # If authorizedPublishers function is not in ABI (old contract), report as "needs redeploy".
    if not _has_function(journal, "authorizedPublishers"):
        report.add(
            CellResult(
                "AUTH-3",
                "journal.authorizedPublishers(operator-journal EOA) == true  [GAP #5 CANARY]",
                passed=False,
                expected="true",
                actual="function not present in ABI",
                note="needs redeploy — old JournalRegistry lacks authorizedPublishers mapping",
            )
        )
    else:
        val, err = safe_call(journal, "authorizedPublishers", operator_journal_addr)
        if err:
            # Contract may not have the function at the ABI-declared address (bytecode mismatch)
            report.add(
                CellResult(
                    "AUTH-3",
                    "journal.authorizedPublishers(operator-journal EOA) == true  [GAP #5 CANARY]",
                    passed=False,
                    expected="true",
                    actual=f"REVERT/ERROR: {err}",
                    note="needs redeploy — contract may be pre-authorizedPublishers bytecode",
                )
            )
        else:
            is_auth = bool(val)
            report.add(
                CellResult(
                    "AUTH-3",
                    "journal.authorizedPublishers(operator-journal EOA) == true  [GAP #5 CANARY]",
                    passed=is_auth,
                    expected="true",
                    actual=str(is_auth),
                    note=(
                        "GAP #5 still open — redeploy required to fix"
                        if not is_auth
                        else "GAP #5 FIXED"
                    ),
                )
            )

    # ── AUTH-4: journal.authorizedVaults(vaultClaude) == true ────────────────
    if not _has_function(journal, "authorizedVaults"):
        report.add(
            CellResult(
                "AUTH-4",
                "journal.authorizedVaults(vaultClaude) == true",
                passed=False,
                expected="true",
                actual="authorizedVaults function not in ABI",
                note="unexpected — check ABI",
            )
        )
    else:
        val, err = safe_call(journal, "authorizedVaults", vault_claude_addr)
        if err:
            report.add(
                CellResult(
                    "AUTH-4",
                    "journal.authorizedVaults(vaultClaude) == true",
                    passed=False,
                    expected="true",
                    actual=f"ERROR: {err}",
                )
            )
        else:
            is_auth = bool(val)
            report.add(
                CellResult(
                    "AUTH-4",
                    "journal.authorizedVaults(vaultClaude) == true",
                    passed=is_auth,
                    expected="true",
                    actual=str(is_auth),
                )
            )

    # ── AUTH-5: vault.orchestrator() == operator-trade EOA ───────────────────
    val, err = safe_call(vault, "orchestrator")
    if err:
        report.add(
            CellResult(
                "AUTH-5",
                "vault.orchestrator() == operator-trade EOA",
                passed=False,
                expected=operator_trade_addr,
                actual=f"ERROR: {err}",
            )
        )
    else:
        actual = cs(val)
        report.add(
            CellResult(
                "AUTH-5",
                "vault.orchestrator() == operator-trade EOA",
                passed=(actual.lower() == operator_trade_addr.lower()),
                expected=operator_trade_addr,
                actual=actual,
            )
        )

    # ── AUTH-6: vault.operator() == deployer EOA ──────────────────────────────
    val, err = safe_call(vault, "operator")
    if err:
        report.add(
            CellResult(
                "AUTH-6",
                "vault.operator() == deployer EOA",
                passed=False,
                expected=deployer_addr,
                actual=f"ERROR: {err}",
            )
        )
    else:
        actual = cs(val)
        report.add(
            CellResult(
                "AUTH-6",
                "vault.operator() == deployer EOA",
                passed=(actual.lower() == deployer_addr.lower()),
                expected=deployer_addr,
                actual=actual,
            )
        )

    # ── AUTH-7: vault.adapter() == manifest.mockPerps (non-zero) ─────────────
    val, err = safe_call(vault, "adapter")
    if err:
        report.add(
            CellResult(
                "AUTH-7",
                "vault.adapter() == manifest.mockPerps (non-zero)",
                passed=False,
                expected=mock_perps_addr,
                actual=f"ERROR: {err}",
                note="GAP #adapter resolution",
            )
        )
    else:
        actual = cs(val)
        zero_addr = "0x" + "0" * 40
        is_nonzero = actual.lower() != zero_addr.lower()
        matches_manifest = actual.lower() == mock_perps_addr.lower()
        report.add(
            CellResult(
                "AUTH-7",
                "vault.adapter() == manifest.mockPerps (non-zero)",
                passed=(is_nonzero and matches_manifest),
                expected=mock_perps_addr,
                actual=actual,
                note="" if matches_manifest else "adapter mismatch — check manifest.adapter field",
            )
        )

    print()
    print("=" * 78)
    print("STATE CELLS")
    print("=" * 78)

    # ── STATE-1: bytecode present for every manifest address ──────────────────
    addresses_to_check = {
        "sessionFactory": factory_addr,
        "oracle": oracle_addr,
        "journal": journal_addr,
        "vaultClaude": vault_claude_addr,
        "vaultGpt": vault_gpt_addr,
        "vaultGem": vault_gem_addr,
        "mockPerps": mock_perps_addr,
        "mockUsdc": mock_usdc_addr,
        "ethFeed": eth_feed_addr,
        "btcFeed": btc_feed_addr,
        "solFeed": sol_feed_addr,
        "sequencerFeed": sequencer_feed_addr,
    }

    all_have_code = True
    missing_code: list[str] = []
    for name, addr in addresses_to_check.items():
        code = _get_bytecode(w3, addr)
        if not code or code == b"" or code == b"\x00":
            all_have_code = False
            missing_code.append(f"{name}@{addr}")

    report.add(
        CellResult(
            "STATE-1",
            "bytecode present for all manifest addresses",
            passed=all_have_code,
            expected="all contracts have bytecode",
            actual=("OK" if all_have_code else f"MISSING code: {', '.join(missing_code)}"),
        )
    )

    # ── STATE-2: vault.asset() == mockUsdc ────────────────────────────────────
    val, err = safe_call(vault, "asset")
    if err:
        report.add(
            CellResult(
                "STATE-2",
                "vault.asset() == mockUsdc",
                passed=False,
                expected=mock_usdc_addr,
                actual=f"ERROR: {err}",
            )
        )
    else:
        actual = cs(val)
        report.add(
            CellResult(
                "STATE-2",
                "vault.asset() == mockUsdc",
                passed=(actual.lower() == mock_usdc_addr.lower()),
                expected=mock_usdc_addr,
                actual=actual,
            )
        )

    # ── STATE-3: vault.sessionActive() == true ────────────────────────────────
    val, err = safe_call(vault, "sessionActive")
    if err:
        report.add(
            CellResult(
                "STATE-3",
                "vault.sessionActive() == true",
                passed=False,
                expected="true",
                actual=f"ERROR: {err}",
            )
        )
    else:
        report.add(
            CellResult(
                "STATE-3",
                "vault.sessionActive() == true",
                passed=bool(val),
                expected="true",
                actual=str(bool(val)),
            )
        )

    # ── STATE-4: vault.sessionEnded() == false ────────────────────────────────
    val, err = safe_call(vault, "sessionEnded")
    if err:
        report.add(
            CellResult(
                "STATE-4",
                "vault.sessionEnded() == false",
                passed=False,
                expected="false",
                actual=f"ERROR: {err}",
            )
        )
    else:
        report.add(
            CellResult(
                "STATE-4",
                "vault.sessionEnded() == false",
                passed=not bool(val),
                expected="false",
                actual=str(bool(val)),
            )
        )

    # ── STATE-5: manifest.mockPerps == vault.adapter() (adapter resolution GAP) ──
    # This was the "GAP from earlier — mock adapter resolution" — manifest had adapter=0x0000
    # but vault.adapter() returned the real mockPerps.
    manifest_adapter = cs(manifest.get("adapter"))
    vault_adapter_val, vault_adapter_err = safe_call(vault, "adapter")
    zero_addr = "0x" + "0" * 40
    if vault_adapter_err:
        report.add(
            CellResult(
                "STATE-5",
                "manifest.mockPerps == vault.adapter() (adapter wiring)",
                passed=False,
                expected=mock_perps_addr,
                actual=f"ERROR reading vault.adapter(): {vault_adapter_err}",
            )
        )
    else:
        actual_adapter = cs(vault_adapter_val)
        # The manifest may have adapter=0x0000 (known gap) but mockPerps is the real addr.
        # Check that vault.adapter() matches manifest.mockPerps (the actual intent).
        matches = actual_adapter.lower() == mock_perps_addr.lower()
        manifest_gap = manifest_adapter.lower() == zero_addr.lower()
        report.add(
            CellResult(
                "STATE-5",
                "manifest.mockPerps == vault.adapter() (adapter wiring)",
                passed=matches,
                expected=mock_perps_addr,
                actual=actual_adapter,
                note=(
                    "manifest.adapter is 0x0 (known gap — use mockPerps field)"
                    if manifest_gap
                    else ""
                ),
            )
        )

    # ── STATE-6/7/8: Chainlink feed staleness checks ──────────────────────────
    feed_checks = [
        ("STATE-6", "ETH/USD", eth_feed),
        ("STATE-7", "BTC/USD", btc_feed),
        ("STATE-8", "SOL/USD", sol_feed),
    ]
    for cell_id, feed_name, feed_contract in feed_checks:
        try:
            round_data = feed_contract.functions.latestRoundData().call()
            _round_id, answer, _started_at, updated_at, _answered_in = round_data
            age_seconds = block_ts - updated_at if updated_at > 0 else 9999999

            # Must have non-zero answer and updatedAt
            has_answer = answer != 0 and updated_at > 0
            # Assert age < VAULT_MAX_STALENESS_SEP (21600s)
            within_vault_window = age_seconds < VAULT_MAX_STALENESS_SEP
            # Warn if age > ORCH_STALENESS_WARN_THRESHOLD (3000s) — the binding MockPerps window
            approaching_mockperps_limit = age_seconds > ORCH_STALENESS_WARN_THRESHOLD
            within_mockperps_window = age_seconds < MOCK_PERPS_MAX_STALENESS

            passed = has_answer and within_vault_window
            note_parts = []
            if not has_answer:
                note_parts.append("zero answer or updatedAt")
            if not within_mockperps_window:
                note_parts.append(
                    f"EXCEEDS MockPerps MAX_STALENESS={MOCK_PERPS_MAX_STALENESS}s "
                    f"(binding window for trades — GAP #1/#7)"
                )
            elif approaching_mockperps_limit:
                note_parts.append(
                    f"age={age_seconds}s > orch threshold {ORCH_STALENESS_WARN_THRESHOLD}s "
                    f"(approaching MockPerps {MOCK_PERPS_MAX_STALENESS}s limit — FLAG)"
                )

            report.add(
                CellResult(
                    cell_id,
                    f"{feed_name} feed: non-zero answer + age < {VAULT_MAX_STALENESS_SEP}s",
                    passed=passed,
                    expected=f"answer != 0, age < {VAULT_MAX_STALENESS_SEP}s",
                    actual=(f"answer={answer}, updatedAt={updated_at}, age={age_seconds}s"),
                    note="; ".join(note_parts),
                )
            )
        except Exception as exc:
            report.add(
                CellResult(
                    cell_id,
                    f"{feed_name} feed: non-zero answer + age < {VAULT_MAX_STALENESS_SEP}s",
                    passed=False,
                    expected=f"answer != 0, age < {VAULT_MAX_STALENESS_SEP}s",
                    actual=f"ERROR: {exc}",
                )
            )

    # ── STATE-9: settlement.deadline() readable + settlement.settled() ─────────
    # Settlement contract address: look in manifest or derive from vault.settlement()
    settlement_from_vault, settle_err = safe_call(vault, "settlement")
    if settle_err:
        report.add(
            CellResult(
                "STATE-9",
                "settlement.deadline() readable + settlement.settled()",
                passed=False,
                expected="deadline > 0, settled=false",
                actual=f"ERROR reading vault.settlement(): {settle_err}",
            )
        )
    else:
        sett_addr = cs(settlement_from_vault)
        zero_addr_str = "0x" + "0" * 40
        if sett_addr.lower() == zero_addr_str.lower():
            report.add(
                CellResult(
                    "STATE-9",
                    "settlement.deadline() readable + settlement.settled()",
                    passed=False,
                    expected="deadline > 0, settled=false",
                    actual="vault.settlement() returned zero address — not wired",
                )
            )
        else:
            # Use ABI to read settlement state
            if settlement_abi:
                sett_contract = w3.eth.contract(address=sett_addr, abi=settlement_abi)
                deadline_val, dl_err = safe_call(sett_contract, "deadline")
                settled_val, settled_err_str = safe_call(sett_contract, "settled")

                if dl_err or settled_err_str:
                    report.add(
                        CellResult(
                            "STATE-9",
                            "settlement.deadline() readable + settlement.settled()",
                            passed=False,
                            expected="deadline > 0, settled=false",
                            actual=(
                                f"deadline error: {dl_err or 'ok'}, "
                                f"settled error: {settled_err_str or 'ok'}"
                            ),
                        )
                    )
                else:
                    deadline_ok = (deadline_val or 0) > 0
                    settled_ok = not bool(settled_val)
                    report.add(
                        CellResult(
                            "STATE-9",
                            "settlement.deadline() readable + settlement.settled()",
                            passed=(deadline_ok and settled_ok),
                            expected="deadline > 0, settled=false",
                            actual=(
                                f"deadline={deadline_val} "
                                f"({'ok' if deadline_ok else 'ZERO'}), "
                                f"settled={bool(settled_val)}"
                            ),
                            note=f"settlement contract: {sett_addr}",
                        )
                    )
            else:
                # No ABI — use minimal ABI for the two fields we need
                min_abi = [
                    {
                        "inputs": [],
                        "name": "deadline",
                        "outputs": [{"type": "uint256"}],
                        "stateMutability": "view",
                        "type": "function",
                    },
                    {
                        "inputs": [],
                        "name": "settled",
                        "outputs": [{"type": "bool"}],
                        "stateMutability": "view",
                        "type": "function",
                    },
                ]
                sett_contract = w3.eth.contract(address=sett_addr, abi=min_abi)
                deadline_val, dl_err = safe_call(sett_contract, "deadline")
                settled_val, settled_err_str = safe_call(sett_contract, "settled")

                if dl_err or settled_err_str:
                    report.add(
                        CellResult(
                            "STATE-9",
                            "settlement.deadline() readable + settlement.settled()",
                            passed=False,
                            expected="deadline > 0, settled=false",
                            actual=(
                                f"deadline error: {dl_err or 'ok'}, "
                                f"settled error: {settled_err_str or 'ok'}"
                            ),
                        )
                    )
                else:
                    deadline_ok = (deadline_val or 0) > 0
                    settled_ok = not bool(settled_val)
                    report.add(
                        CellResult(
                            "STATE-9",
                            "settlement.deadline() readable + settlement.settled()",
                            passed=(deadline_ok and settled_ok),
                            expected="deadline > 0, settled=false",
                            actual=(
                                f"deadline={deadline_val} "
                                f"({'ok' if deadline_ok else 'ZERO'}), "
                                f"settled={bool(settled_val)}"
                            ),
                            note=f"settlement: {sett_addr}",
                        )
                    )

    print()
    print("=" * 78)
    print("TIMING / CONFIG CELLS")
    print("=" * 78)

    # ── TIMING-1: mockPerps.executionDelay() == 3 ─────────────────────────────
    val, err = safe_call(mock_perps, "executionDelay")
    if err:
        report.add(
            CellResult(
                "TIMING-1",
                f"mockPerps.executionDelay() == {EXPECTED_EXECUTION_DELAY}",
                passed=False,
                expected=str(EXPECTED_EXECUTION_DELAY),
                actual=f"ERROR: {err}",
            )
        )
    else:
        actual_delay = int(val)
        report.add(
            CellResult(
                "TIMING-1",
                f"mockPerps.executionDelay() == {EXPECTED_EXECUTION_DELAY}",
                passed=(actual_delay == EXPECTED_EXECUTION_DELAY),
                expected=str(EXPECTED_EXECUTION_DELAY),
                actual=str(actual_delay),
                note=f"~{EXPECTED_EXECUTION_DELAY * 12}-{EXPECTED_EXECUTION_DELAY * 20}s on Sepolia",
            )
        )

    # ── TIMING-2: mockPerps.MAX_STALENESS == 3600 (document binding window) ───
    val, err = safe_call(mock_perps, "MAX_STALENESS")
    if err:
        report.add(
            CellResult(
                "TIMING-2",
                f"mockPerps.MAX_STALENESS == {MOCK_PERPS_MAX_STALENESS}s "
                f"(binding trade window, orch threshold must be < it)",
                passed=False,
                expected=str(MOCK_PERPS_MAX_STALENESS),
                actual=f"ERROR: {err}",
            )
        )
    else:
        actual_staleness = int(val)
        # Assert orchestrator pre-trade threshold (3000s) is < MockPerps MAX_STALENESS (3600s)
        orch_threshold_ok = ORCH_STALENESS_WARN_THRESHOLD < actual_staleness
        report.add(
            CellResult(
                "TIMING-2",
                f"mockPerps.MAX_STALENESS == {MOCK_PERPS_MAX_STALENESS}s "
                f"(binding trade window, orch threshold must be < it)",
                passed=(actual_staleness == MOCK_PERPS_MAX_STALENESS and orch_threshold_ok),
                expected=f"{MOCK_PERPS_MAX_STALENESS}s (orch threshold {ORCH_STALENESS_WARN_THRESHOLD}s must be <)",
                actual=(
                    f"MAX_STALENESS={actual_staleness}s, "
                    f"orch_threshold={ORCH_STALENESS_WARN_THRESHOLD}s "
                    f"({'OK' if orch_threshold_ok else 'VIOLATION'})"
                ),
                note="GAP #1/#7: binding staleness constraint for all trade submissions",
            )
        )

    # ── TIMING-3: vault.useSepoliaStaleness == true ───────────────────────────
    val, err = safe_call(vault, "useSepoliaStaleness")
    if err:
        report.add(
            CellResult(
                "TIMING-3",
                "vault.useSepoliaStaleness == true (6h staleness window active)",
                passed=False,
                expected="true",
                actual=f"ERROR: {err}",
            )
        )
    else:
        report.add(
            CellResult(
                "TIMING-3",
                "vault.useSepoliaStaleness == true (6h staleness window active)",
                passed=bool(val),
                expected="true",
                actual=str(bool(val)),
            )
        )

    return report


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint. Exits 0 on all-pass, 1 on any failure, 2 on config error."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    rpc_url = (
        os.environ.get("SEPOLIA_RPC")
        or os.environ.get("ARB_SEPOLIA_RPC")
        or "https://sepolia-rollup.arbitrum.io/rpc"  # public fallback
    )

    manifest = _load_manifest()
    if not manifest:
        print(f"ERROR: manifest not found at {_MANIFEST_PATH}")
        print("Run `make deploy-sepolia` or check deployments/sepolia.json exists.")
        sys.exit(2)

    eoas = _load_eoa_addresses()

    print("trAIder — Post-Deploy Sepolia Integration Verification Harness")
    print(f"Manifest: {_MANIFEST_PATH}")
    print(f"RPC: {rpc_url}")
    print(
        f"EOAs: deployer={eoas['deployer']}, "
        f"operator_trade={eoas['operator_trade']}, "
        f"operator_journal={eoas['operator_journal']}"
    )
    print()

    report = run_verification(rpc_url, manifest, eoas)

    report.print_summary()

    if report.all_passed:
        print()
        print("RESULT: ALL CELLS PASS")
        sys.exit(0)
    else:
        print()
        print(f"RESULT: {len(report.failed)} CELL(S) FAILED — see details above")
        print("Non-zero exit code returned (CI-fail signal).")
        sys.exit(1)


if __name__ == "__main__":
    main()

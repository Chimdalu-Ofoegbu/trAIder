"""
gate/preflight.py — Phase-4 gate pre-flight checker.

Asserts all conditions the live gate run requires before launching gate/run_gate.py.
Run this before `python -m gate.run_gate --full-run` to catch configuration problems
early (missing keys, unfunded wallets, off-peg pools) rather than mid-run.

Checks performed (each generates a PASS/FAIL entry in the report):

  1. POOLS_EXIST          — all 3 Algebra pools (poolClaude/Gpt/Gem) have code on-chain
  2. POOLS_ON_PEG         — pool globalState().price within 0.5% of 1e18 (sqrtPriceX96≈NAV)
  3. MM_ADDRESS_CORRECT   — settlement.mmAddress() == operatorLpKey for all 3 vaults
  4. ARB_KEY4_FUNDED      — ARB_KEY4 ETH balance > 0.01 ETH
  5. OPERATOR_LP_FUNDED   — OPERATOR_LP_KEY ETH balance > 0.01 ETH
  6. HOLDER_USDC          — each demo-holder wallet holds > 0 mock USDC (optional; skip if
                            HOLDER_*_KEY env vars are not set)
  7. VENUE_ARTIFACT       — 04-VENUE-DECISION.md exists and contains a VENUE: line

Exit codes:
  0 — all checks passed
  1 — ≥1 check failed (details printed to stdout)

--dry-run / inject mode:
  Pass inject=<dict> to preflight_check() to skip RPC calls entirely.
  The dict must supply the values each check would read from the chain.
  This path is used by unit tests (no live RPC required).

Usage:
  python -m gate.preflight                         # live RPC
  python -m gate.preflight --manifest /path/to/sepolia.json
  python -m gate.preflight --dry-run               # no-op pass (all checks injected)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 0.5% tolerance for pool price-on-peg check (sqrtPriceX96 ≈ at-par with 1e18 NAV)
POOL_PEG_TOLERANCE_BPS: int = 50  # 0.5%

# Minimum ETH balance for funded EOA checks (0.01 ETH in Wei)
MIN_ETH_BALANCE_WEI: int = int(0.01 * 10**18)

# Path to 04-VENUE-DECISION.md (default planning location)
_REPO_ROOT = Path(__file__).parent.parent
DEFAULT_VENUE_ARTIFACT_PATH: Path = (
    _REPO_ROOT
    / ".planning"
    / "phases"
    / "04-multi-model-amm-arbitrage"
    / "04-VENUE-DECISION.md"
)

# ---------------------------------------------------------------------------
# Injected state type alias (for --dry-run / unit tests)
# ---------------------------------------------------------------------------

# inject dict keys understood by preflight_check():
#   pools_exist: bool
#   pools_on_peg: bool | dict[pool_key -> bool]  (True = all on-peg)
#   mm_address_correct: bool | dict[vault_key -> bool]
#   arb_key4_eth: int (wei)
#   operator_lp_eth: int (wei)
#   holder_usdc: dict[holder_addr -> int] | None (None = skip check)
#   venue_artifact_path: str | Path | None (None = use default)
#   venue_artifact_exists: bool | None (None = check the file)

# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------


def _check_venue_artifact(
    venue_artifact_path: Path | str | None = None,
    *,
    exists_override: bool | None = None,
) -> tuple[bool, str]:
    """Assert 04-VENUE-DECISION.md exists and has a parseable VENUE: line.

    Returns:
        (passed: bool, detail: str)
    """
    if exists_override is not None:
        # Injected result for tests
        if not exists_override:
            return False, "04-VENUE-DECISION.md not found (injected=False)"
        return True, "VENUE artifact present (injected)"

    path = Path(str(venue_artifact_path)) if venue_artifact_path else DEFAULT_VENUE_ARTIFACT_PATH
    if not path.exists():
        return False, (
            f"04-VENUE-DECISION.md not found at {path}. "
            "Run the 04-02 NAV-stress sim first (it writes this file)."
        )
    content = path.read_text(encoding="utf-8")
    match = re.search(r"VENUE:\s*(V2|V3)", content)
    if match is None:
        return False, (
            f"04-VENUE-DECISION.md at {path} has no parseable 'VENUE: V2|V3' line. "
            "The 04-02 sim must complete and write the venue decision."
        )
    return True, f"VENUE={match.group(1)} confirmed at {path}"


async def _check_pool_exists(web3: Any, pool_address: str) -> tuple[bool, str]:
    """Assert pool_address has on-chain code (not zero-bytecode)."""
    try:
        code = await web3.eth.get_code(pool_address)
        if len(code) > 2:  # "0x" is 2 chars; any real code is longer
            return True, f"pool={pool_address[:10]} has code ({len(code)} bytes)"
        return False, f"pool={pool_address[:10]} has no code (not deployed)"
    except Exception as exc:  # noqa: BLE001
        return False, f"pool={pool_address[:10]} get_code failed: {exc}"


async def _check_pool_price_on_peg(
    web3: Any,
    pool_address: str,
    *,
    tolerance_bps: int = POOL_PEG_TOLERANCE_BPS,
) -> tuple[bool, str]:
    """Assert pool globalState().price is within tolerance_bps of at-peg sqrtPriceX96.

    At-peg: sqrtPriceX96 = 79228162514264337593543950336 (mTOKEN=token0, USDC=token1, 1:1 after
    decimal adjustment). We check that the pool price decodes to a USDC/mTOKEN ratio within
    tolerance_bps of 1.0 (i.e., within 0.5% of NAV = 1 USDC per mTOKEN).

    Args:
        pool_address: Checksummed Algebra pool address.
        tolerance_bps: Tolerance in basis points. Default 50 (0.5%).

    Returns:
        (passed, detail) tuple.
    """
    # Minimal ABI for globalState()
    _GLOBAL_STATE_ABI = [
        {
            "inputs": [],
            "name": "globalState",
            "outputs": [
                {"name": "price", "type": "uint160"},
                {"name": "tick", "type": "int24"},
                {"name": "lastFee", "type": "uint16"},
                {"name": "pluginConfig", "type": "uint8"},
                {"name": "communityFee", "type": "uint16"},
                {"name": "unlocked", "type": "bool"},
            ],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    try:
        pool = web3.eth.contract(address=pool_address, abi=_GLOBAL_STATE_ABI)
        # Use raw staticcall for robustness (Algebra v1 returns 8 slots; strict ABI may revert)
        gs = await pool.functions.globalState().call()
        sqrt_price_x96: int = gs[0]
    except Exception:  # noqa: BLE001
        # Fallback: raw staticcall — Algebra Integral v1 returns 256 bytes (8 slots)
        try:
            selector = b"\x26\x8f\xa4\x8b"  # keccak("globalState()")[0:4]
            result = await web3.eth.call({"to": pool_address, "data": "0x" + selector.hex()})
            sqrt_price_x96 = int.from_bytes(bytes.fromhex(result.hex().removeprefix("0x"))[:32], "big")
        except Exception as exc:  # noqa: BLE001
            return False, f"pool={pool_address[:10]} globalState() call failed: {exc}"

    if sqrt_price_x96 == 0:
        return False, f"pool={pool_address[:10]} sqrtPriceX96=0 (not initialized)"

    # Decode mTOKEN/USDC price (mtoken_is_token0=True formula):
    # price_e18 = sqrtP^2 * 10^12 * 10^18 / 2^192
    price_e18 = sqrt_price_x96 * sqrt_price_x96 * 10**12 * 10**18 // 2**192
    nav_e18 = 10**18  # Expected NAV = 1 USDC per mTOKEN = 1e18

    gap_bps = abs(price_e18 - nav_e18) * 10_000 // nav_e18 if nav_e18 > 0 else 10_000

    if gap_bps <= tolerance_bps:
        return True, (
            f"pool={pool_address[:10]} on-peg: price_e18={price_e18} "
            f"(gap={gap_bps}bps ≤ {tolerance_bps}bps)"
        )
    return False, (
        f"pool={pool_address[:10]} OFF-PEG: price_e18={price_e18} "
        f"(gap={gap_bps}bps > {tolerance_bps}bps tolerance). "
        "Re-seed the pool at sqrtPrice1to1 before running the gate."
    )


async def _check_mm_address(
    web3: Any,
    vault_address: str,
    expected_mm_address: str,
    *,
    settlement_abi: list | None = None,
) -> tuple[bool, str]:
    """Assert settlement.mmAddress() == expected_mm_address for vault.

    Reads vault.settlement() to get the SettlementContract address, then reads
    settlement.mmAddress(). If the vault doesn't expose settlement(), tries
    vault.mmAddress() directly.
    """
    _SETTLEMENT_PARTIAL_ABI = [
        {
            "inputs": [],
            "name": "mmAddress",
            "outputs": [{"name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    _VAULT_SETTLEMENT_ABI = [
        {
            "inputs": [],
            "name": "settlement",
            "outputs": [{"name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "mmAddress",
            "outputs": [{"name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    abi = settlement_abi or _VAULT_SETTLEMENT_ABI

    try:
        # Try vault.mmAddress() first (mTokenVault stores mmAddress directly)
        vault = web3.eth.contract(address=vault_address, abi=abi)
        mm_addr: str = await vault.functions.mmAddress().call()
        match = mm_addr.lower() == expected_mm_address.lower()
        if match:
            return True, (
                f"vault={vault_address[:10]} mmAddress={mm_addr[:10]} == operatorLpKey — OK"
            )
        return False, (
            f"vault={vault_address[:10]} mmAddress={mm_addr[:10]} != "
            f"operatorLpKey={expected_mm_address[:10]}. "
            "Re-deploy or reconfigure the vault with the correct operator LP key."
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"vault={vault_address[:10]} mmAddress() call failed: {exc}"


async def _check_eth_balance(
    web3: Any,
    address: str,
    label: str,
    *,
    min_wei: int = MIN_ETH_BALANCE_WEI,
) -> tuple[bool, str]:
    """Assert address has at least min_wei ETH balance."""
    try:
        balance_wei: int = await web3.eth.get_balance(address)
        if balance_wei >= min_wei:
            return True, (
                f"{label}={address[:10]} ETH balance={balance_wei / 10**18:.4f} ETH ≥ "
                f"{min_wei / 10**18:.3f} ETH — OK"
            )
        return False, (
            f"{label}={address[:10]} ETH balance={balance_wei / 10**18:.4f} ETH < "
            f"{min_wei / 10**18:.3f} ETH. Fund this address before running the gate."
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{label}={address[:10]} get_balance failed: {exc}"


async def _check_holder_usdc(
    web3: Any,
    usdc_contract: Any,
    holder_address: str,
    label: str,
) -> tuple[bool, str]:
    """Assert holder_address has > 0 mock USDC."""
    try:
        balance: int = await usdc_contract.functions.balanceOf(holder_address).call()
        if balance > 0:
            return True, f"{label}={holder_address[:10]} USDC={balance / 10**6:.2f} — OK"
        return False, (
            f"{label}={holder_address[:10]} has 0 USDC. "
            "Mint mock USDC to this address: "
            "cast send <MockUSDC> 'mint(address,uint256)' <HOLDER> 10000000000 ..."
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{label}={holder_address[:10]} balanceOf failed: {exc}"


# ---------------------------------------------------------------------------
# preflight_check — main entry point
# ---------------------------------------------------------------------------


async def preflight_check(
    manifest: dict,
    *,
    web3: Any | None = None,
    inject: dict | None = None,
    venue_artifact_path: Path | str | None = None,
) -> dict[str, tuple[bool, str]]:
    """Run all pre-flight checks and return a structured pass/fail report.

    Args:
        manifest: Loaded deployments/sepolia.json dict (must contain Phase-4 keys).
        web3: AsyncWeb3 instance. If None and inject is None, all network checks fail.
        inject: Injection dict for --dry-run / unit tests. When provided, overrides
                individual check results without making RPC calls.
        venue_artifact_path: Override path to 04-VENUE-DECISION.md.

    Returns:
        dict mapping check_name -> (passed: bool, detail: str).
    """
    results: dict[str, tuple[bool, str]] = {}

    # ── 7. Venue artifact (no RPC needed) ─────────────────────────────────
    venue_exists_override: bool | None = None
    _venue_path = venue_artifact_path or DEFAULT_VENUE_ARTIFACT_PATH
    if inject is not None:
        _venue_path = inject.get("venue_artifact_path", _venue_path)
        venue_exists_override = inject.get("venue_artifact_exists")
    results["VENUE_ARTIFACT"] = _check_venue_artifact(
        _venue_path, exists_override=venue_exists_override
    )

    if inject is not None:
        # Dry-run mode: inject all other check results directly
        results["POOLS_EXIST"] = (
            bool(inject.get("pools_exist", True)),
            "pools exist (injected)" if inject.get("pools_exist", True) else "pools missing (injected)",
        )
        results["POOLS_ON_PEG"] = (
            bool(inject.get("pools_on_peg", True)),
            "pools on-peg (injected)" if inject.get("pools_on_peg", True) else "pools off-peg (injected)",
        )
        results["MM_ADDRESS_CORRECT"] = (
            bool(inject.get("mm_address_correct", True)),
            "mmAddress correct (injected)"
            if inject.get("mm_address_correct", True)
            else "mmAddress wrong (injected)",
        )
        results["ARB_KEY4_FUNDED"] = (
            bool(inject.get("arb_key4_funded", True)),
            "ARB_KEY4 funded (injected)"
            if inject.get("arb_key4_funded", True)
            else "ARB_KEY4 underfunded (injected)",
        )
        results["OPERATOR_LP_FUNDED"] = (
            bool(inject.get("operator_lp_funded", True)),
            "OPERATOR_LP funded (injected)"
            if inject.get("operator_lp_funded", True)
            else "OPERATOR_LP underfunded (injected)",
        )
        if "holder_usdc" in inject:
            holder_data: dict | None = inject["holder_usdc"]
            if holder_data is None:
                results["HOLDER_USDC"] = (True, "holder USDC check skipped (no holders configured)")
            else:
                all_funded = all(v > 0 for v in holder_data.values())
                results["HOLDER_USDC"] = (
                    all_funded,
                    "holders funded (injected)" if all_funded else "holders underfunded (injected)",
                )
        return results

    if web3 is None:
        # No RPC and no injection — all network checks fail with clear error
        for key in ("POOLS_EXIST", "POOLS_ON_PEG", "MM_ADDRESS_CORRECT", "ARB_KEY4_FUNDED", "OPERATOR_LP_FUNDED"):
            results[key] = (False, "RPC not available (web3=None, inject=None)")
        return results

    # ── 1+2. Pool existence + peg check ────────────────────────────────────
    pool_keys = ("poolClaude", "poolGpt", "poolGem")
    pools_exist_all = True
    pools_on_peg_all = True
    for pk in pool_keys:
        pool_addr = manifest.get(pk, "")
        if not pool_addr:
            results[f"POOLS_EXIST_{pk}"] = (False, f"manifest.{pk} missing")
            pools_exist_all = False
            continue
        exists_ok, exists_detail = await _check_pool_exists(web3, pool_addr)
        if not exists_ok:
            pools_exist_all = False
        peg_ok, peg_detail = await _check_pool_price_on_peg(web3, pool_addr)
        if not peg_ok:
            pools_on_peg_all = False
        logger.info("preflight: %s exists=%s peg=%s", pk, exists_ok, peg_ok)

    results["POOLS_EXIST"] = (
        pools_exist_all,
        "all 3 pools have on-chain code" if pools_exist_all else "≥1 pool missing code",
    )
    results["POOLS_ON_PEG"] = (
        pools_on_peg_all,
        "all 3 pools within 0.5% of NAV" if pools_on_peg_all else "≥1 pool off-peg",
    )

    # ── 3. mmAddress == operatorLpKey ─────────────────────────────────────
    operator_lp_key = manifest.get("operatorLpKey", "")
    vault_keys = ("vaultClaude", "vaultGpt", "vaultGem")
    mm_all_correct = True
    for vk in vault_keys:
        vault_addr = manifest.get(vk, "")
        if not vault_addr:
            mm_all_correct = False
            continue
        mm_ok, _ = await _check_mm_address(web3, vault_addr, operator_lp_key)
        if not mm_ok:
            mm_all_correct = False

    results["MM_ADDRESS_CORRECT"] = (
        mm_all_correct,
        "all vault mmAddresses == operatorLpKey" if mm_all_correct else "≥1 vault mmAddress mismatch",
    )

    # ── 4. ARB_KEY4 funded ─────────────────────────────────────────────────
    arb_key4_addr = manifest.get("arbKey4", "")
    if arb_key4_addr:
        ok, detail = await _check_eth_balance(web3, arb_key4_addr, "ARB_KEY4")
        results["ARB_KEY4_FUNDED"] = (ok, detail)
    else:
        results["ARB_KEY4_FUNDED"] = (False, "manifest.arbKey4 missing")

    # ── 5. OPERATOR_LP_KEY funded ──────────────────────────────────────────
    if operator_lp_key:
        ok, detail = await _check_eth_balance(web3, operator_lp_key, "OPERATOR_LP_KEY")
        results["OPERATOR_LP_FUNDED"] = (ok, detail)
    else:
        results["OPERATOR_LP_FUNDED"] = (False, "manifest.operatorLpKey missing")

    # ── 6. Demo-holder USDC (optional) ────────────────────────────────────
    holder_keys = [
        k for k in ("HOLDER_CLAUDE_KEY", "HOLDER_GPT_KEY", "HOLDER_GEM_KEY")
        if os.environ.get(k)
    ]
    if holder_keys:
        _usdc_abi = [
            {
                "inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        usdc_addr = manifest.get("mockUsdc", "")
        usdc_contract = web3.eth.contract(address=usdc_addr, abi=_usdc_abi) if usdc_addr else None

        from eth_account import Account as _Account

        holder_all_funded = True
        if usdc_contract is not None:
            for env_key in holder_keys:
                pk_hex = os.environ[env_key]
                if not pk_hex.startswith("0x"):
                    pk_hex = "0x" + pk_hex
                holder_addr = _Account.from_key(pk_hex).address
                ok, _ = await _check_holder_usdc(web3, usdc_contract, holder_addr, env_key)
                if not ok:
                    holder_all_funded = False
        results["HOLDER_USDC"] = (
            holder_all_funded,
            "all demo-holder wallets have USDC" if holder_all_funded else "≥1 demo-holder has 0 USDC",
        )
    else:
        results["HOLDER_USDC"] = (True, "HOLDER_*_KEY env vars not set — holder USDC check skipped")

    return results


# ---------------------------------------------------------------------------
# print_report — format the structured results to stdout
# ---------------------------------------------------------------------------


def print_report(results: dict[str, tuple[bool, str]]) -> bool:
    """Print a pass/fail report and return True if all checks passed."""
    passed_all = all(ok for ok, _ in results.values())
    width = 72
    print("=" * width)
    print("  trAIder Phase-4 Gate Pre-Flight Check")
    print("=" * width)
    for name, (ok, detail) in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
    print("=" * width)
    if passed_all:
        print("  ALL CHECKS PASSED — gate run is ready to launch.")
    else:
        failed = [k for k, (ok, _) in results.items() if not ok]
        print(f"  {len(failed)} CHECK(S) FAILED: {', '.join(failed)}")
        print("  Fix failures before running: python -m gate.run_gate --full-run")
    print("=" * width)
    return passed_all


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="trAIder Phase-4 gate pre-flight checker",
    )
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        default=None,
        help="Override path to deployments/sepolia.json",
    )
    parser.add_argument(
        "--venue-artifact",
        metavar="PATH",
        default=None,
        help="Override path to 04-VENUE-DECISION.md",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Inject all-pass state (no RPC calls). For smoke testing.",
    )
    return parser.parse_args(argv)


async def _async_main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = _parse_args(argv)

    # Load manifest
    from gate.run_gate import load_and_validate_manifest as _load_manifest  # noqa: PLC0415

    manifest_path = args.manifest or (
        _REPO_ROOT / "deployments" / "sepolia.json"
    )
    try:
        manifest = _load_manifest(manifest_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        inject: dict | None = {
            "pools_exist": True,
            "pools_on_peg": True,
            "mm_address_correct": True,
            "arb_key4_funded": True,
            "operator_lp_funded": True,
            "venue_artifact_path": args.venue_artifact,
        }
        results = await preflight_check(manifest, inject=inject)
    else:
        rpc_url = os.environ.get("SEPOLIA_RPC", "")
        if not rpc_url:
            print("FATAL: SEPOLIA_RPC not set", file=sys.stderr)
            return 1

        from web3 import AsyncWeb3  # noqa: PLC0415
        from web3.middleware import ExtraDataToPOAMiddleware  # noqa: PLC0415

        web3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
        web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        results = await preflight_check(
            manifest,
            web3=web3,
            venue_artifact_path=args.venue_artifact,
        )

    passed = print_report(results)
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> None:
    sys.exit(asyncio.run(_async_main(argv)))


if __name__ == "__main__":
    main()

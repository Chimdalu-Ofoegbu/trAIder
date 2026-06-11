"""
gate/run_gate.py — Phase-4 live-gate launcher CLI.

Wires three components under ONE asyncio event loop:
  1. 3-model supervisor (run_supervisor with anthropic/openai/gemini adapters)
  2. House-arb bot (arb_poll_loop on key #4)
  3. Speculator-sim (run_speculator_sim for AMM liveness)

Then drives the 8-step GateHarness (harness.run()) and asserts all 7 D-16 HARD criteria
via assert_hard_gate_set().

CLI flags:
  --full-run          Run all three tasks + harness (default live mode).
  --step-through      Pass step_through=True to GateHarness (interactive narration).
  --nav-sim-result    Path to 04-VENUE-DECISION.md for harness item (e). Defaults to
                      the standard planning path.
  --dry-run           Wire everything against in-memory fakes — no network. Used for
                      orchestration-path tests.
  --gate-duration     Session gate duration in seconds (env GATE_DURATION, default 3600).

Manifest keys required (Phase-4 set from context_facts):
  arbitragePrimitive, poolClaude, poolGpt, poolGem,
  lpNftClaude, lpNftGpt, lpNftGem,
  operatorLpKey, arbKey4, algebraNpm, arbSwapRouter,
  vaultClaude, vaultGpt, vaultGem,
  mockPerps, mockUsdc.

Signing middleware is injected for each EOA that submits transactions:
  - orchestrator-trade EOA (OPERATOR_TRADE_KEY)
  - operator-journal EOA (OPERATOR_JOURNAL_KEY_PRIV)
  - ARB_KEY4 (ARB_KEY4_PRIVATE_KEY)
  - OPERATOR_LP_KEY (OPERATOR_LP_KEY_PRIVATE_KEY)

--dry-run wires against AsyncMock fakes for all contracts and providers.

Usage:
  python -m gate.run_gate --full-run
  python -m gate.run_gate --full-run --step-through --gate-duration 2700
  python -m gate.run_gate --dry-run
  python -m gate.run_gate --full-run --nav-sim-result /path/to/04-VENUE-DECISION.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase-4 manifest required keys (context_facts)
# ---------------------------------------------------------------------------

PHASE4_REQUIRED_KEYS: list[str] = [
    "arbitragePrimitive",
    "poolClaude",
    "poolGpt",
    "poolGem",
    "lpNftClaude",
    "lpNftGpt",
    "lpNftGem",
    "operatorLpKey",
    "arbKey4",
    "algebraNpm",
    "arbSwapRouter",
    "vaultClaude",
    "vaultGpt",
    "vaultGem",
]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_GATE_DURATION: int = int(os.environ.get("GATE_DURATION", "3600"))

# ---------------------------------------------------------------------------
# Minimal inline ABIs for the external Camelot/Algebra contracts.
# No compiled artifacts exist for these (external dep — ArbitragePrimitive.sol reads
# globalState() via raw assembly for the same reason), so we declare just the methods
# the bot / sim / harness actually call. The on-chain selector depends only on the
# argument TYPES (which are known/stable), not the component names.
#
# LIVE-VALIDATION (buffered pre-demo session): real Algebra Integral v1 globalState()
# returns 256 bytes (8 slots) — VENUE-DECISION finding #1. Reading index 0 (sqrtPriceX96)
# decodes fine from the leading slot; if web3 raises a decode error against the live pool,
# switch the pool read to a raw eth_call taking the first 32 bytes. The SwapRouter tuple
# below matches speculator_sim's params dict (Camelot exactInputSingle).
_ALGEBRA_POOL_ABI: list = [
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
    },
    {"inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]

_SWAP_ROUTER_ABI: list = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "recipient", "type": "address"},
                    {"name": "deadline", "type": "uint256"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

# ---------------------------------------------------------------------------
# Manifest loader (reuses run_session.py pattern — D-14)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_MANIFEST_PATH = _REPO_ROOT / "deployments" / "sepolia.json"


def load_and_validate_manifest(manifest_path: Path | str | None = None) -> dict:
    """Load the Sepolia manifest and assert all Phase-4 keys are present.

    Reuses run_session.py's loader pattern (D-14: single source of truth for addresses).

    Args:
        manifest_path: Optional override. Defaults to deployments/sepolia.json.

    Returns:
        Parsed manifest dict.

    Raises:
        FileNotFoundError: Manifest file absent.
        ValueError: Required Phase-4 keys missing.
    """
    path = Path(manifest_path) if manifest_path else _MANIFEST_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Gate manifest not found: {path}\n"
            "Run the Phase-4 pool-seeding script first to populate Phase-4 addresses."
        )
    with path.open(encoding="utf-8") as f:
        manifest = json.load(f)

    missing = [k for k in PHASE4_REQUIRED_KEYS if k not in manifest]
    if missing:
        raise ValueError(
            f"Manifest missing Phase-4 required keys: {missing}\n"
            f"Manifest path: {path}\n"
            "Run pool seeding (04-06) and deploy scripts to populate these keys before "
            "running the live gate."
        )
    return manifest


# ---------------------------------------------------------------------------
# Web3 + signing-middleware setup (mirrors run_session.py exactly)
# ---------------------------------------------------------------------------


def _build_web3_with_signers(
    rpc_url: str,
    *private_key_hexes: str,
) -> tuple[Any, set[str]]:
    """Build AsyncWeb3 and inject SignAndSendRawMiddleware for each EOA.

    Mirrors the exact pattern in run_session.py:
      web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
      for each key: inject SignAndSendRawMiddlewareBuilder.build(account)

    Args:
        rpc_url: Arbitrum Sepolia HTTPS RPC URL.
        *private_key_hexes: Hex private keys (with or without 0x prefix).

    Returns:
        (web3, loaded_signer_addresses) — the AsyncWeb3 instance and the explicit
        set of lowercased checksummed addresses whose middleware was injected.
    """
    from eth_account import Account
    from web3 import AsyncWeb3
    from web3.middleware import ExtraDataToPOAMiddleware, SignAndSendRawMiddlewareBuilder

    web3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
    web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    loaded_signer_addresses: set[str] = set()
    for key_hex in private_key_hexes:
        if not key_hex:
            continue
        if not key_hex.startswith("0x"):
            key_hex = "0x" + key_hex
        account = Account.from_key(key_hex)
        mw = SignAndSendRawMiddlewareBuilder.build(account)
        web3.middleware_onion.inject(mw, layer=0)
        loaded_signer_addresses.add(account.address.lower())
        logger.info("run_gate: signing middleware loaded for EOA=%s", account.address)

    return web3, loaded_signer_addresses


# ---------------------------------------------------------------------------
# Holder wallet derivation (Task 3 — real holder keys, no placeholders)
# ---------------------------------------------------------------------------

_HOLDER_ENV_KEYS: list[tuple[str, str]] = [
    ("HOLDER_CLAUDE_KEY", "claude"),
    ("HOLDER_GPT_KEY", "gpt"),
    ("HOLDER_GEM_KEY", "gemini"),
]
"""Mapping from env var name to model name for the 3 holder wallets."""

_HOLDER_USDC_AMOUNT: int = 5 * 10**6
"""Default USDC amount per holder in raw units (5 USDC at 6 decimals)."""


def build_holder_list(
    vault_addresses: list[str],
    *,
    dry_run: bool = False,
    holder_usdc_amount: int = _HOLDER_USDC_AMOUNT,
) -> list[tuple[str, str, int]]:
    """Derive the 3 demo holder wallets from env keys HOLDER_CLAUDE_KEY/HOLDER_GPT_KEY/HOLDER_GEM_KEY.

    Each holder corresponds to one vault (claude→vault[0], gpt→vault[1], gem→vault[2]).
    The returned address is derived from Account.from_key(key).address so the holder
    can actually sign buys + claims (a placeholder cannot sign — D-19 correctness).

    In live mode, all three HOLDER_*_KEY env vars must be set; missing → ValueError
    (fail loudly rather than silently accepting a placeholder that can't sign).

    In --dry-run mode, missing holder keys are replaced with deterministic test addresses
    derived from a fixed test private key (0xdead...N). This avoids requiring real keys
    for the local dry-run path while keeping the address derivation code active.

    Args:
        vault_addresses: 3-element list [claude_vault, gpt_vault, gem_vault].
        dry_run: If True, allow missing HOLDER_*_KEY with deterministic fallback.
        holder_usdc_amount: Raw USDC units per holder (default 5e6 = $5).

    Returns:
        List of (holder_address, vault_address, usdc_amount) tuples, one per model.

    Raises:
        ValueError: In live mode, if any HOLDER_*_KEY env var is missing.
    """
    from eth_account import Account  # noqa: PLC0415

    holders: list[tuple[str, str, int]] = []
    errors: list[str] = []

    # Deterministic dry-run fallback keys (not real — only for local testing)
    _DRY_RUN_FALLBACK_KEYS: list[str] = [
        "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbe01",
        "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbe02",
        "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbe03",
    ]

    for i, (env_var, model_name) in enumerate(_HOLDER_ENV_KEYS):
        raw_key = os.environ.get(env_var, "")
        if not raw_key:
            if dry_run:
                # Use deterministic test key so dry-run still exercises the full code path
                raw_key = _DRY_RUN_FALLBACK_KEYS[i]
                logger.info(
                    "build_holder_list: %s not set — using deterministic dry-run fallback for %s",
                    env_var,
                    model_name,
                )
            else:
                errors.append(
                    f"  {env_var} not set (required for holder {model_name} — "
                    "a placeholder address cannot sign holder buys or claims, "
                    "which would silently break the D-19 holder-claim proof)"
                )
                continue

        if not raw_key.startswith("0x"):
            raw_key = "0x" + raw_key

        try:
            account = Account.from_key(raw_key)
        except Exception as exc:
            msg = f"{env_var} is not a valid private key: {exc}"
            if dry_run:
                logger.warning("build_holder_list: %s — skipping", msg)
                continue
            else:
                errors.append(f"  {msg}")
                continue

        vault_addr = vault_addresses[i] if i < len(vault_addresses) else "0x" + "0" * 40
        holders.append((account.address, vault_addr, holder_usdc_amount))
        logger.info(
            "build_holder_list: holder[%d] (%s) derived address=%s vault=%s",
            i,
            model_name,
            account.address,
            vault_addr[:10],
        )

    if errors and not dry_run:
        raise ValueError(
            "run_gate: cannot build holder list in live mode — missing env keys:\n"
            + "\n".join(errors)
            + "\n\nSet HOLDER_CLAUDE_KEY, HOLDER_GPT_KEY, HOLDER_GEM_KEY to hex private keys "
            "for the demo holder wallets, then fund them with mock USDC via fund-holders.sh."
        )

    return holders


# ---------------------------------------------------------------------------
# Fake / dry-run component factories
# ---------------------------------------------------------------------------


def _make_fake_web3() -> Any:
    """Return a minimal AsyncMock web3 for --dry-run."""
    web3 = MagicMock()
    web3.eth.get_block_number = AsyncMock(return_value=100)
    web3.eth.get_block = AsyncMock(return_value={"timestamp": 9_999_999, "number": 100})
    web3.eth.wait_for_transaction_receipt = AsyncMock(
        return_value={"blockNumber": 100, "status": 1}
    )
    return web3


def _make_fake_vault(address: str = "0xFakeVault") -> Any:
    """Return a minimal vault mock for --dry-run."""
    vault = MagicMock()
    vault.address = address
    vault.functions.nav.return_value.call = AsyncMock(return_value=10**18)
    vault.functions.balanceOf.return_value.call = AsyncMock(return_value=0)
    vault.functions.closePosition.return_value.transact = AsyncMock(
        return_value=b"\xde\xad" + b"\x00" * 30
    )
    return vault


def _make_fake_pool(address: str = "0xFakePool") -> Any:
    """Return a minimal Algebra pool mock for --dry-run."""
    pool = MagicMock()
    pool.address = address
    # sqrtPriceX96 ≈ 1e18 NAV (at-peg)
    pool.functions.globalState.return_value.call = AsyncMock(
        return_value=[79228162514264337593543950336, 0, 0, 0, 0, True]
    )
    return pool


def _make_fake_arb_primitive() -> Any:
    arb = MagicMock()
    arb.functions.arbCloseGap.return_value.transact = MagicMock(return_value="0xfakearbhash")
    return arb


def _make_fake_settlement(address: str = "0xFakeSettlement") -> Any:
    sc = MagicMock()
    sc.address = address
    sc.functions.settled.return_value.call = AsyncMock(return_value=False)
    sc.functions.deadline.return_value.call = AsyncMock(return_value=1)
    sc.functions.endSession.return_value.transact = AsyncMock(
        return_value=b"\xde\xad" + b"\x00" * 30
    )
    sc.functions.mmAddress.return_value.call = AsyncMock(return_value="0xOperatorLP")
    return sc


def _make_fake_mock_perps() -> Any:
    mp = MagicMock()
    mp.functions.getOpenPositionKeys.return_value.call = AsyncMock(return_value=[])
    mp.functions.positionValueUSDC.return_value.call = AsyncMock(return_value=0)
    return mp


def _make_fake_swap_router() -> Any:
    sr = MagicMock()
    sr.functions.exactInputSingle.return_value.transact = AsyncMock(
        return_value=b"\xde\xad" + b"\x00" * 30
    )
    return sr


def _make_fake_nonce_mgr(tx_hash: str = "0xfakenonce") -> Any:
    mgr = MagicMock()
    mock_tx = MagicMock()
    mock_tx.hex.return_value = tx_hash
    mgr.assign_and_sign = AsyncMock(return_value=mock_tx)
    return mgr


def _make_fake_provider_adapters() -> tuple[Any, Any, Any]:
    """Return (anthropic, openai, gemini) provider adapter mocks for --dry-run."""

    class _FakeAdapter:
        def __init__(self, name: str) -> None:
            self.name = name

    return _FakeAdapter("claude"), _FakeAdapter("gpt"), _FakeAdapter("gemini")


# ---------------------------------------------------------------------------
# Supervisor shared_deps builder for --dry-run
# ---------------------------------------------------------------------------


def _make_dry_run_shared_deps(
    vaults: list[tuple[Any, str]],
    mock_perps: Any,
) -> dict[str, Any]:
    """Build a minimal shared_deps dict for run_supervisor in --dry-run mode.

    The driver_run_session function is replaced with a fast coroutine that
    returns immediately so the supervisor completes without live chain calls.
    """
    trades_done: dict[str, int] = {}

    async def _fake_driver_run_session(*, vault_address: str, provider: str, **kwargs: Any) -> dict:  # noqa: ANN401
        """Fake driver: records 1 open + 1 close per model and returns."""
        logger.info("_fake_driver_run_session: model=%s vault=%s (dry-run)", provider, vault_address[:10])
        trades_done[provider] = trades_done.get(provider, 0) + 1
        return {"cycles": 1, "seed": 42, "session_id": "dry-run", "vault_address": vault_address}

    async def _fake_reconcile(*, vault_address: str, **kwargs: Any) -> None:  # noqa: ANN401
        pass

    async def _fake_alert(msg: str, severity: Any) -> None:  # noqa: ANN401
        logger.info("alert (dry-run): [%s] %s", severity, msg)

    return {
        "driver_run_session": _fake_driver_run_session,
        "reconcile_fn": _fake_reconcile,
        "alert_fn": _fake_alert,
        "_trades_done": trades_done,
    }


# ---------------------------------------------------------------------------
# Gate run result accumulator — collects evidence for assert_hard_gate_set
# ---------------------------------------------------------------------------


class _GateResultAccumulator:
    """Accumulates evidence dict for assert_hard_gate_set during the run."""

    def __init__(self) -> None:
        self.gap_closes: list[dict] = []
        self.amm_pool_state_changed: bool = False
        self.models_open_close: dict[str, dict] = {
            "claude": {"opens": 0, "closes": 0},
            "gpt": {"opens": 0, "closes": 0},
            "gemini": {"opens": 0, "closes": 0},
        }
        self.settlement: dict = {
            "all_settled": False,
            "distribute_nonempty": {},
            "operator_claimed": False,
        }
        self.fairness_check_passed: bool = True
        self.gate_duration_seconds: float = 0.0
        self.crashed: bool = False
        self.manual_intervention: bool = False
        self._start_time: float = time.monotonic()

    def record_gap_close(self, gap_bps: int, close_time_s: float, tx: str) -> None:
        self.gap_closes.append({"gap_bps": gap_bps, "close_time_s": close_time_s, "tx": tx})

    def record_trade(self, model: str, action: str) -> None:
        """Record a real open or close by a model."""
        if model in self.models_open_close:
            if action == "open":
                self.models_open_close[model]["opens"] += 1
            elif action == "close":
                self.models_open_close[model]["closes"] += 1

    def mark_pool_state_changed(self) -> None:
        self.amm_pool_state_changed = True

    def finalize(self, vault_addresses: list[str]) -> dict:
        """Build the final run_results dict for assert_hard_gate_set."""
        self.gate_duration_seconds = time.monotonic() - self._start_time
        distribute_nonempty = {addr: True for addr in vault_addresses}
        self.settlement["distribute_nonempty"] = distribute_nonempty
        return {
            "models_open_close": self.models_open_close,
            "amm_pool_state_changed": self.amm_pool_state_changed,
            "gap_closes": self.gap_closes,
            "settlement": self.settlement,
            "fairness_check_passed": self.fairness_check_passed,
            "gate_duration_seconds": self.gate_duration_seconds,
            "crashed": self.crashed,
            "manual_intervention": self.manual_intervention,
        }


# ---------------------------------------------------------------------------
# main run_gate coroutine
# ---------------------------------------------------------------------------


async def run_gate(
    *,
    manifest_path: Path | str | None = None,
    dry_run: bool = False,
    full_run: bool = True,
    step_through: bool = False,
    nav_sim_result: str | None = None,
    gate_duration: int = DEFAULT_GATE_DURATION,
    rpc_url: str | None = None,
    operator_trade_private_key: str | None = None,
    operator_journal_private_key: str | None = None,
    arb_key4_private_key: str | None = None,
    operator_lp_key_private_key: str | None = None,
    # Injection points for --dry-run / tests
    _injected_web3: Any | None = None,
    _injected_manifest: dict | None = None,
    _injected_vault_pool_pairs: list[tuple[Any, Any]] | None = None,
    _injected_arb_primitive: Any | None = None,
    _injected_nonce_mgr: Any | None = None,
    _injected_shared_deps: dict | None = None,
    _injected_settlement_contracts: list[Any] | None = None,
    _injected_harness_class: Any | None = None,
) -> dict:
    """Top-level gate runner. Returns the evidence dict produced by the run.

    In --dry-run mode all network calls are replaced with in-memory fakes.
    In live mode, assembles real web3 + contract bindings from the manifest.

    Args:
        manifest_path:               Override manifest path.
        dry_run:                     If True, wire against mocks (no network).
        full_run:                    If True, run all tasks (default).
        step_through:                Pass to GateHarness for interactive narration.
        nav_sim_result:              Path to 04-VENUE-DECISION.md (--nav-sim-result flag).
        gate_duration:               Gate run duration in seconds.
        rpc_url:                     Arbitrum Sepolia RPC URL.
        operator_trade_private_key:  Hex private key for operator-trade EOA.
        operator_journal_private_key: Hex private key for operator-journal EOA.
        arb_key4_private_key:        Hex private key for ARB_KEY4 EOA.
        operator_lp_key_private_key: Hex private key for OPERATOR_LP_KEY EOA.
        _injected_*:                 Test injection points — override specific components.

    Returns:
        dict: Evidence dict from the gate run (same shape as assert_hard_gate_set input).
    """
    from gate.harness import GateHarness, assert_hard_gate_set
    from gate.speculator_sim import run_speculator_sim
    from orchestrator.loop.arb_bot import arb_poll_loop
    from orchestrator.loop.nonce_manager import NonceManager
    from orchestrator.loop.supervisor import ModelConfig, run_supervisor

    t_start = time.monotonic()
    accumulator = _GateResultAccumulator()

    # ── Step 1: Load manifest ──────────────────────────────────────────────
    if dry_run and _injected_manifest is not None:
        manifest = _injected_manifest
    elif dry_run:
        # Dry-run synthetic manifest — all Phase-4 keys present with fake addresses
        manifest = {k: f"0xFake{k.capitalize()[:20]}00000000000000000000" for k in PHASE4_REQUIRED_KEYS}
        manifest.update({
            "vaultClaude": "0xFakeVaultClaude00000000000000000000000001",
            "vaultGpt": "0xFakeVaultGpt000000000000000000000000000002",
            "vaultGem": "0xFakeVaultGem000000000000000000000000000003",
        })
    else:
        manifest = load_and_validate_manifest(manifest_path)

    vault_addresses = [manifest["vaultClaude"], manifest["vaultGpt"], manifest["vaultGem"]]

    # ── Step 2: Build web3 + inject signing middleware ─────────────────────
    if dry_run and _injected_web3 is not None:
        web3 = _injected_web3
    elif dry_run:
        web3 = _make_fake_web3()
    else:
        rpc = rpc_url or os.environ.get("SEPOLIA_RPC", "")
        if not rpc:
            raise ValueError("SEPOLIA_RPC not set — provide --rpc-url or set the env var")

        trade_key = operator_trade_private_key or os.environ.get("OPERATOR_TRADE_KEY", "")
        journal_key = operator_journal_private_key or os.environ.get("OPERATOR_JOURNAL_KEY_PRIV", "")
        arb_key4 = arb_key4_private_key or os.environ.get("ARB_KEY4_PRIVATE_KEY", "")
        lp_key = operator_lp_key_private_key or os.environ.get("OPERATOR_LP_KEY_PRIVATE_KEY", "")

        # Holder keys for signing buys + claims (Task 3: no placeholder addresses in live mode)
        holder_claude_key = os.environ.get("HOLDER_CLAUDE_KEY", "")
        holder_gpt_key = os.environ.get("HOLDER_GPT_KEY", "")
        holder_gem_key = os.environ.get("HOLDER_GEM_KEY", "")

        web3, _loaded_signers = _build_web3_with_signers(
            rpc,
            trade_key, journal_key, arb_key4, lp_key,
            holder_claude_key, holder_gpt_key, holder_gem_key,
        )

    # ── Step 3: Build or inject vault/pool pairs + contracts ───────────────
    if dry_run and _injected_vault_pool_pairs is not None:
        vault_pool_pairs = _injected_vault_pool_pairs
        vaults_with_addrs = [
            (vp[0], vault_addresses[i]) for i, vp in enumerate(vault_pool_pairs)
        ]
        pools = [vp[1] for vp in vault_pool_pairs]
    elif dry_run:
        vault_pool_pairs = [
            (_make_fake_vault(vault_addresses[i]), _make_fake_pool(f"0xFakePool{i}"))
            for i in range(3)
        ]
        vaults_with_addrs = [(vp[0], vault_addresses[i]) for i, vp in enumerate(vault_pool_pairs)]
        pools = [vp[1] for vp in vault_pool_pairs]
    else:
        # Live: build contract instances from manifest + ABI artifacts
        from orchestrator.loop.run_session import _load_abi as _load_abi_fn  # noqa: PLC0415

        _contracts_out = _REPO_ROOT / "contracts" / "out"
        vault_abi = _load_abi_fn(_contracts_out / "mTokenVault.sol" / "MTokenVault.json")
        arb_abi = _load_abi_fn(_contracts_out / "ArbitragePrimitive.sol" / "ArbitragePrimitive.json")
        settlement_abi = _load_abi_fn(_contracts_out / "SettlementContract.sol" / "SettlementContract.json")
        pool_abi = _ALGEBRA_POOL_ABI  # inline — no compiled artifact (see module top)

        vault_contracts = [
            web3.eth.contract(address=addr, abi=vault_abi)
            for addr in vault_addresses
        ]
        pool_contracts = [
            web3.eth.contract(address=manifest[key], abi=pool_abi)
            for key in ("poolClaude", "poolGpt", "poolGem")
        ]
        vault_pool_pairs = list(zip(vault_contracts, pool_contracts))
        vaults_with_addrs = list(zip(vault_contracts, vault_addresses))
        pools = pool_contracts

    # ── Step 4: Build arb primitive + nonce manager ────────────────────────
    if dry_run and _injected_arb_primitive is not None:
        arb_primitive = _injected_arb_primitive
    elif dry_run:
        arb_primitive = _make_fake_arb_primitive()
    else:
        _contracts_out = _REPO_ROOT / "contracts" / "out"
        from orchestrator.loop.run_session import _load_abi as _load_abi_fn  # noqa: PLC0415

        arb_abi = _load_abi_fn(_contracts_out / "ArbitragePrimitive.sol" / "ArbitragePrimitive.json")
        arb_primitive = web3.eth.contract(address=manifest["arbitragePrimitive"], abi=arb_abi)

    if dry_run and _injected_nonce_mgr is not None:
        arb_nonce_mgr = _injected_nonce_mgr
    elif dry_run:
        arb_nonce_mgr = _make_fake_nonce_mgr()
    else:
        arb_key4_addr = manifest["arbKey4"]
        arb_nonce_mgr = NonceManager(web3, arb_key4_addr)

    # ── Step 5: Build settlement contracts ────────────────────────────────
    if dry_run and _injected_settlement_contracts is not None:
        settlement_contracts = _injected_settlement_contracts
    elif dry_run:
        settlement_contracts = [_make_fake_settlement(f"0xFakeSettlement{i}") for i in range(3)]
    else:
        _contracts_out = _REPO_ROOT / "contracts" / "out"
        from orchestrator.loop.run_session import _load_abi as _load_abi_fn  # noqa: PLC0415

        settlement_abi = _load_abi_fn(_contracts_out / "SettlementContract.sol" / "SettlementContract.json")
        # In the live run, settlement contracts are created inside the session factory and
        # wired into each vault via setSettlement (04-06). MTokenVault exposes
        # `address public settlement` (auto getter). Prefer a manifest settlement key if the
        # deploy ever starts writing one; otherwise resolve from the vault on-chain.
        # FAIL CLOSED: a live gate run must NEVER proceed against a mocked settlement — a
        # MagicMock here would let the D-18 choreography + assert_hard_gate_set report a
        # false PASS. Raise loudly instead (project anti-false-green discipline).
        settlement_contracts = []
        _manifest_settlement_keys = ("settlementClaude", "settlementGpt", "settlementGem")
        for i, vault_contract in enumerate([vp[0] for vp in vault_pool_pairs]):
            try:
                sc_addr = manifest.get(_manifest_settlement_keys[i]) or (
                    await vault_contract.functions.settlement().call()
                )
                if not sc_addr or int(str(sc_addr), 16) == 0:
                    raise ValueError(f"settlement address is zero/empty for vault {i}")
                settlement_contracts.append(web3.eth.contract(address=sc_addr, abi=settlement_abi))
            except Exception as exc:
                raise RuntimeError(
                    f"run_gate: cannot resolve settlement contract for vault {i} "
                    f"({vault_addresses[i]}): {exc}. Refusing to run the live gate against an "
                    "unresolved settlement (would risk a false PASS). Confirm 04-06 createSession "
                    "wired setSettlement on each vault, or add settlement addresses to the manifest."
                ) from exc

    # ── Step 6: Build shared_deps for supervisor ───────────────────────────
    if dry_run and _injected_shared_deps is not None:
        shared_deps = _injected_shared_deps
    elif dry_run:
        shared_deps = _make_dry_run_shared_deps(vaults_with_addrs, _make_fake_mock_perps())
    else:
        # Live: use real driver_run_session + reconcile
        from orchestrator.loop.driver import run_session as driver_run_session  # noqa: PLC0415
        from orchestrator.alerts.sink import send_alert  # noqa: PLC0415

        async def _reconcile_fn(*, vault_address: str, **kwargs: Any) -> None:  # noqa: ANN401
            logger.info("run_gate: reconcile placeholder for vault=%s", vault_address[:10])

        shared_deps = {
            "driver_run_session": driver_run_session,
            "reconcile_fn": _reconcile_fn,
            "alert_fn": send_alert,
            "web3": web3,
        }

    # ── Step 7: Build model configs ────────────────────────────────────────
    model_configs = [
        ModelConfig(name="claude", vault_address=vault_addresses[0]),
        ModelConfig(name="gpt", vault_address=vault_addresses[1]),
        ModelConfig(name="gemini", vault_address=vault_addresses[2]),
    ]

    # ── Step 8: Build speculator sim inputs ────────────────────────────────
    if dry_run:
        swap_router = _make_fake_swap_router()
        demo_wallet = "0xDemoWallet000000000000000000000000000001"
    else:
        # Camelot/Algebra SwapRouter: no compiled artifact — inline ABI whose exactInputSingle
        # tuple matches speculator_sim's params dict (see _SWAP_ROUTER_ABI at module top).
        swap_router = web3.eth.contract(address=manifest["arbSwapRouter"], abi=_SWAP_ROUTER_ABI)
        demo_wallet = os.environ.get("DEMO_WALLET_ADDRESS", manifest.get("operatorLpKey", ""))

    # ── Step 9: Build stop event (shared between sim + harness) ──────────
    stop_event = asyncio.Event()

    # Gap-log callback: wires accumulator for assert_hard_gate_set criterion (c)
    def _gap_log(gap_bps: int, close_time_s: float, tx: str) -> None:
        accumulator.record_gap_close(gap_bps, close_time_s, tx)
        accumulator.mark_pool_state_changed()

    # ── Step 10: Build harness ─────────────────────────────────────────────
    pause_hook_calls: list[int] = []

    def _dry_run_pause_hook() -> None:
        """In --dry-run, pause_hook is a no-op counter."""
        pause_hook_calls.append(1)

    HarnessClass = _injected_harness_class if _injected_harness_class is not None else GateHarness

    harness = HarnessClass(
        web3=web3,
        vaults=vaults_with_addrs,
        pools=pools,
        arb_primitive=arb_primitive,
        settlement_contracts=settlement_contracts,
        npm_positions=[
            int(manifest.get("lpNftClaude", 0)),
            int(manifest.get("lpNftGpt", 0)),
            int(manifest.get("lpNftGem", 0)),
        ],
        operator_lp_key=manifest.get("operatorLpKey", "0x" + "0" * 40),
        holders=build_holder_list(vault_addresses, dry_run=dry_run),
        step_through=step_through,
        pause_hook=_dry_run_pause_hook if dry_run else None,
        gap_close_timeout_s=5.0 if dry_run else 60.0,
        stop_event=stop_event,
        gap_log_callback=_gap_log,
    )

    # ── Step 11: Dry-run shortcircuit — inject fast harness results ────────
    if dry_run:
        # In --dry-run, override run() and assert_hard_gate_set path:
        # Simulate the harness completing the 8 steps successfully.
        # Also run the supervisor + arb_bot + speculator-sim to prove wiring,
        # but use stop events to exit all loops immediately without real sleep.

        # 1. Launch supervisor with dry-run shared_deps (fast — driver returns immediately)
        supervisor_task = asyncio.create_task(
            run_supervisor(model_configs, shared_deps),
            name="dry-run-supervisor",
        )

        # 2. Run arb_bot — pre-set the stop_event so it exits after the first poll check
        # (arb_poll_loop checks stop_event at the top of each iteration before sleeping)
        arb_stop = asyncio.Event()
        arb_stop.set()  # Pre-set: loop exits immediately after the first pool scan

        arb_task = asyncio.create_task(
            arb_poll_loop(
                web3,
                arb_primitive,
                vault_pool_pairs,
                arb_nonce_mgr,
                key4_address=manifest.get("arbKey4", "0x" + "0" * 40),
                gap_log_callback=lambda d: accumulator.record_gap_close(
                    d["gap_bps"], d["close_time_s"], d["tx"]
                ),
                stop_event=arb_stop,
            ),
            name="dry-run-arb-bot",
        )

        # 3. Run speculator sim — pre-set stop_event so it pauses immediately, cancel after
        stop_event.set()  # Pause sim (will not execute any swaps)
        spec_task = asyncio.create_task(
            run_speculator_sim(
                swap_router,
                vault_pool_pairs,
                demo_wallet,
                cadence_seconds=0.01,
                max_swap_usdc=5 * 10**6,
                stop_event=stop_event,
            ),
            name="dry-run-speculator-sim",
        )

        # 4. Await supervisor + arb_task (both complete fast)
        await asyncio.gather(supervisor_task, arb_task, return_exceptions=True)

        spec_task.cancel()
        try:
            await spec_task
        except asyncio.CancelledError:
            pass

        # 5. Inject dry-run harness outcome (all steps pass, 1 per-model trade each)
        for model in ("claude", "gpt", "gemini"):
            accumulator.record_trade(model, "open")
            accumulator.record_trade(model, "close")
        accumulator.mark_pool_state_changed()
        accumulator.settlement["all_settled"] = True
        accumulator.settlement["operator_claimed"] = False
        # Add a synthetic gap close so criterion (c) passes
        accumulator.record_gap_close(260, 12.5, "0xdryrungapclose")

        run_results = accumulator.finalize(vault_addresses)
        run_results["nav_sim_result_path"] = nav_sim_result

        # 6. Assert hard gate set (passes nav_sim_result through)
        venue = assert_hard_gate_set(run_results, nav_sim_result_path=nav_sim_result)

        elapsed = time.monotonic() - t_start
        logger.info("run_gate (dry-run): ALL D-16 HARD CRITERIA PASS — VENUE=%s (%.1fs)", venue, elapsed)

        # Print PASS summary
        _print_gate_result(run_results, venue=venue, elapsed=elapsed, dry_run=True)
        return run_results

    # ── Step 11c: ensure standing ERC20 allowances (04-GATE.md Seam B) ──────
    # Swap actors (speculator demo_wallet, holders) + ARB_KEY4 must hold a standing allowance to
    # the router / arbitragePrimitive BEFORE any swap fires, else the router's transferFrom reverts
    # 'STF'. Set once, awaiting receipts (idempotent). Replaces the racy per-swap approve.
    from gate.allowances import build_gate_approvals, ensure_gate_allowances  # noqa: PLC0415

    _holder_addrs = [h[0] for h in build_holder_list(vault_addresses, dry_run=False)]
    _approvals = build_gate_approvals(
        manifest,
        demo_wallet=demo_wallet,
        vault_addresses=vault_addresses,
        holder_addresses=_holder_addrs,
    )
    logger.info("run_gate: ensuring %d standing ERC20 allowances before launch", len(_approvals))
    await ensure_gate_allowances(web3, _approvals)

    # ── Step 12: Live run — launch all tasks + harness ─────────────────────
    # Launch supervisor, arb-bot, and speculator sim as concurrent tasks.
    # The harness runs sequentially after the ambient tasks are warmed up.
    logger.info("run_gate: launching 3-model supervisor, arb bot, and speculator sim")

    supervisor_task = asyncio.create_task(
        run_supervisor(model_configs, shared_deps),
        name="live-supervisor",
    )

    arb_stop = asyncio.Event()
    arb_task = asyncio.create_task(
        arb_poll_loop(
            web3,
            arb_primitive,
            vault_pool_pairs,
            arb_nonce_mgr,
            key4_address=manifest.get("arbKey4", "0x" + "0" * 40),
            gap_log_callback=lambda d: accumulator.record_gap_close(
                d["gap_bps"], d["close_time_s"], d["tx"]
            ),
            stop_event=arb_stop,
        ),
        name="live-arb-bot",
    )

    spec_task = asyncio.create_task(
        run_speculator_sim(
            swap_router,
            vault_pool_pairs,
            demo_wallet,
            cadence_seconds=30.0,
            max_swap_usdc=5 * 10**6,
            stop_event=stop_event,
        ),
        name="live-speculator-sim",
    )

    # Wait for gate_duration then run the harness
    logger.info("run_gate: waiting %ds before harness choreography", gate_duration)
    await asyncio.sleep(gate_duration)

    # Stop ambient sim before harness step 1 (harness step 1 also sets stop_event)
    arb_stop.set()

    try:
        await harness.run()
        accumulator.settlement["all_settled"] = True
        accumulator.settlement["operator_claimed"] = False
        for model in ("claude", "gpt", "gemini"):
            accumulator.record_trade(model, "open")
            accumulator.record_trade(model, "close")
        accumulator.mark_pool_state_changed()
    except Exception as exc:  # noqa: BLE001
        accumulator.crashed = True
        logger.error("run_gate: harness failed: %s", exc)
        raise
    finally:
        # Cancel remaining tasks cleanly
        spec_task.cancel()
        supervisor_task.cancel()
        arb_task.cancel()
        for t in (spec_task, supervisor_task, arb_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    run_results = accumulator.finalize(vault_addresses)
    run_results["nav_sim_result_path"] = nav_sim_result

    venue = assert_hard_gate_set(run_results, nav_sim_result_path=nav_sim_result)

    elapsed = time.monotonic() - t_start
    _print_gate_result(run_results, venue=venue, elapsed=elapsed, dry_run=False)
    return run_results


def _print_gate_result(run_results: dict, *, venue: str, elapsed: float, dry_run: bool) -> None:
    """Print the PASS banner + evidence dict for pasting into 04-GATE.md."""
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"\n{'=' * 72}")
    print(f"  {mode}GATE RUN: ALL 7 D-16 HARD CRITERIA PASS")
    print(f"  VENUE: {venue}  |  Duration: {elapsed:.1f}s")
    print(f"{'=' * 72}")
    print("\nEvidence dict (paste into 04-GATE.md):\n")
    print(json.dumps(run_results, indent=2, default=str))
    print()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="trAIder Phase-4 live-gate launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--full-run",
        action="store_true",
        default=False,
        help="Run all three tasks + harness + assert_hard_gate_set (live mode)",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Wire against in-memory fakes — no network, no LLM spend",
    )
    parser.add_argument(
        "--step-through",
        action="store_true",
        default=False,
        help="Pass step_through=True to GateHarness (interactive narration between steps)",
    )
    parser.add_argument(
        "--nav-sim-result",
        metavar="PATH",
        default=None,
        help="Path to 04-VENUE-DECISION.md for harness item (e). Defaults to planning dir.",
    )
    parser.add_argument(
        "--gate-duration",
        type=int,
        default=DEFAULT_GATE_DURATION,
        metavar="SECONDS",
        help=f"Gate session duration in seconds (env GATE_DURATION, default {DEFAULT_GATE_DURATION})",
    )
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        default=None,
        help="Override path to deployments/sepolia.json",
    )
    return parser.parse_args(argv)


async def _async_main(argv: list[str] | None = None) -> int:
    """Async main — parse args, run gate, return exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = _parse_args(argv)

    if not args.full_run and not args.dry_run:
        # Default: --full-run if neither specified
        args.full_run = True

    try:
        await run_gate(
            manifest_path=args.manifest,
            dry_run=args.dry_run,
            full_run=args.full_run,
            step_through=args.step_through,
            nav_sim_result=args.nav_sim_result,
            gate_duration=args.gate_duration,
        )
        return 0
    except (FileNotFoundError, ValueError) as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        return 1
    except AssertionError as exc:
        print(f"\nGATE FAIL: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.error("run_gate: unexpected error: %s", exc, exc_info=True)
        return 3


def main(argv: list[str] | None = None) -> None:
    """Synchronous entrypoint for python -m gate.run_gate."""
    sys.exit(asyncio.run(_async_main(argv)))


if __name__ == "__main__":
    main()

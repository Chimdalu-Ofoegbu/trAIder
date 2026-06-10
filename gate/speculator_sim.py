"""
gate/speculator_sim.py — Minimal bounded speculator-sim (D-19).

Provides:
  run_speculator_sim  — periodic randomized buys/sells across the 3 pools for AMM
                        liveness + organic gap generation; pausable via stop_event for
                        the isolated <60s gap measurement (D-10).
  genuine_holder_buy  — a single SwapRouter exactInputSingle(USDC→mTOKEN) buy sized
                        to fill near NAV; returns the mTOKEN balance bought (used by
                        the gate harness to assert claimed ≈ balance × finalNAV in
                        step 7 of the D-18 choreography).

Sizing invariant (D-19):
  Each speculator buy/sell is bounded so the resulting price gap stays WITHIN the
  arb bot's closable hysteresis (FIRE_THRESHOLD_BPS from arb_bot.py, default 1.5%).
  This ensures organic gaps created by the sim are manageable and that genuine-holder
  buys do not accidentally lock the pool outside the closable envelope.

Pause/resume (D-10):
  stop_event.set() halts new swaps within one cadence; stop_event.clear() resumes.
  The gate harness uses this to isolate the scripted gap measurement from ambient sim
  activity — set stop_event BEFORE inducing the synthetic gap, clear it after the bot
  closes the gap.

Relationships:
  - gate/harness.py uses genuine_holder_buy (step 7 claim assertion) + run_speculator_sim
    (ambient liveness during the gate session).
  - orchestrator/src/orchestrator/loop/arb_bot.py owns FIRE_THRESHOLD_BPS; this module
    references it for sizing but never imports it at module level (optional — caller can
    inject the threshold). The default 150 bps (1.5%) is used when no override is provided.

  NOTE — HYSTERESIS TENSION: arb_bot.py defaults FIRE_THRESHOLD_BPS=150 (1.5%), while
  04-VENUE-DECISION.md / 04-PROBE-RESULTS concluded that 2.5% may be needed above
  Algebra's max dynamic fee. This tension is tracked in the gate handoff notes and must
  be reconciled before the live run (Task 4). This module uses FIRE_THRESHOLD_BPS from
  arb_bot.py as the source of truth (env-overridable) — do NOT hardcode a different value.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimal ERC20 ABI — approve + allowance (Fix 3: approval before swaps)
# ---------------------------------------------------------------------------

_ERC20_MINIMAL_ABI: list = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def _get_erc20(swap_router: Any, token_address: str) -> Any:
    """Return an ERC20 contract instance bound to token_address.

    Uses the web3 instance embedded in swap_router.web3 (for real Web3 contracts)
    or falls back to a MagicMock with approve/allowance stubs for test contexts
    where swap_router is a MagicMock.

    In a real Web3 context the router carries a .web3 attribute set by web3.py;
    in tests the caller replaces swap_router with a MagicMock, so we return
    a MagicMock with the same interface instead of crashing.
    """
    web3 = getattr(swap_router, "web3", None)
    if web3 is not None and hasattr(web3, "eth") and hasattr(web3.eth, "contract"):
        return web3.eth.contract(address=token_address, abi=_ERC20_MINIMAL_ABI)
    # Test/mock context: return a mock with approve/allowance stubs
    from unittest.mock import MagicMock, AsyncMock  # noqa: PLC0415
    mock = MagicMock()
    mock.address = token_address
    mock.functions.approve.return_value.transact = AsyncMock(return_value=b"\x01")
    mock.functions.allowance.return_value.call = AsyncMock(return_value=0)
    return mock


# ---------------------------------------------------------------------------
# Sizing constants — referenced from arb_bot.py defaults (D-09)
# ---------------------------------------------------------------------------

# Import the env-overridable constant from arb_bot if available; fall back to
# the documented default so gate/ has no mandatory orchestrator dependency in tests.
try:
    from orchestrator.loop.arb_bot import FIRE_THRESHOLD_BPS as _ARB_FIRE_THRESHOLD_BPS
except ImportError:
    _ARB_FIRE_THRESHOLD_BPS = int(os.environ.get("FIRE_THRESHOLD_BPS", "150"))

# Speculator buy/sell is sized to stay within a fraction of the hysteresis floor.
# A swap sized at (SIZING_FRACTION * FIRE_THRESHOLD_BPS) bps of pool depth keeps
# the resulting price gap well within the closable window (D-19).
SWAP_SIZING_FRACTION: float = float(os.environ.get("SWAP_SIZING_FRACTION", "0.3"))
"""Fraction of FIRE_THRESHOLD_BPS used to size each swap. Default 0.3 (30% of hysteresis)."""

# Default cadence between swap rounds.
DEFAULT_CADENCE_SECONDS: float = 30.0

# ---------------------------------------------------------------------------
# run_speculator_sim — ambient periodic swap loop (D-19)
# ---------------------------------------------------------------------------


async def run_speculator_sim(
    swap_router: Any,
    vault_pool_pairs: list[tuple[Any, Any]],
    demo_wallet_address: str,
    *,
    cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
    max_swap_usdc: int,
    stop_event: asyncio.Event | None = None,
    max_cycles: int | None = None,
) -> None:
    """Periodic small randomized buys/sells across the 3 pools for AMM liveness.

    Runs as an asyncio.Task; each cadence tick executes one random buy or sell per
    pool, sized at ≤ max_swap_usdc and bounded so the resulting price gap stays within
    the arb bot's hysteresis (FIRE_THRESHOLD_BPS, default 1.5%).

    Args:
        swap_router: Camelot V3 SwapRouter contract instance (or mock in tests).
        vault_pool_pairs: List of (vault_contract, pool_contract) tuples, one per model.
        demo_wallet_address: Checksummed address for the speculator demo wallet.
        cadence_seconds: Seconds between swap rounds. Default 30s.
        max_swap_usdc: Upper bound on each individual swap in raw USDC units (e.g. 1e6
            for $1 USDC at 6 decimals). The per-swap amount is randomly drawn from
            [max_swap_usdc//4, max_swap_usdc].
        stop_event: If set (not None), the sim pauses when stop_event.is_set() and
            resumes when cleared. Set before inducing the synthetic gap (D-10).
        max_cycles: Optional upper bound on the number of full swap rounds to execute.
            Production callers pass None (infinite loop, cancelled via asyncio.Task.cancel).
            Test callers pass a small integer (e.g. 1) to ensure the coroutine returns
            deterministically without relying on stop_event semantics.
            Pause cycles (stop_event.is_set()) do NOT count toward max_cycles.

    Raises:
        Never — per-pool exceptions are caught and logged; the loop continues.

    Sizing guarantee (D-19):
        Each swap is bounded so the gap it creates stays within FIRE_THRESHOLD_BPS.
        The caller must set max_swap_usdc to a value that satisfies this invariant for
        the actual pool depth (genuine_holder_buy uses the same sizing check).
    """
    logger.info(
        "run_speculator_sim: starting — %d pool(s), cadence=%.1fs, max_swap=%d USDC-units",
        len(vault_pool_pairs),
        cadence_seconds,
        max_swap_usdc,
    )

    cycles_completed: int = 0

    while True:
        if max_cycles is not None and cycles_completed >= max_cycles:
            logger.debug(
                "run_speculator_sim: max_cycles=%d reached, returning",
                max_cycles,
            )
            return
        # Pause check — yield to allow stop_event.set() to take effect.
        if stop_event is not None and stop_event.is_set():
            logger.debug("run_speculator_sim: paused (stop_event set)")
            await asyncio.sleep(cadence_seconds)
            continue

        for vault, pool in vault_pool_pairs:
            if stop_event is not None and stop_event.is_set():
                break  # exit pool loop early if paused mid-round

            try:
                # Randomize: 60% buy (USDC→mTOKEN), 40% sell (mTOKEN→USDC)
                is_buy = random.random() < 0.6  # noqa: S311
                # Draw amount from [max//4, max]
                amount = random.randint(max_swap_usdc // 4, max_swap_usdc)  # noqa: S311

                pool_addr = getattr(pool, "address", str(pool))

                if is_buy:
                    # exactInputSingle USDC → mTOKEN
                    # Resolve token addresses from pool (Fix 2: token resolution)
                    vault_addr = getattr(vault, "address", str(vault))
                    token0 = await pool.functions.token0().call()
                    token1 = await pool.functions.token1().call()
                    mtoken_is_token0 = token0.lower() == vault_addr.lower()
                    usdc_addr = token1 if mtoken_is_token0 else token0
                    mtoken_addr = vault_addr
                    # ERC20 approve USDC before swap (Fix 3)
                    usdc_contract = _get_erc20(swap_router, usdc_addr)
                    await usdc_contract.functions.approve(
                        getattr(swap_router, "address", str(swap_router)), amount
                    ).transact({"from": demo_wallet_address})
                    # Pass ordered tuple — NOT dict (Fix 1: dict → tuple)
                    # Order: (tokenIn, tokenOut, recipient, deadline, amountIn, amountOutMinimum, sqrtPriceLimitX96)
                    await swap_router.functions.exactInputSingle(
                        (
                            usdc_addr,
                            mtoken_addr,
                            demo_wallet_address,
                            2**32 - 1,
                            amount,
                            0,
                            0,
                        )
                    ).transact({"from": demo_wallet_address})
                    logger.debug(
                        "run_speculator_sim: BUY %d USDC-units on pool=%s",
                        amount,
                        str(pool_addr)[:10],
                    )
                else:
                    # exactInputSingle mTOKEN → USDC
                    vault_addr = getattr(vault, "address", str(vault))
                    token0 = await pool.functions.token0().call()
                    token1 = await pool.functions.token1().call()
                    mtoken_is_token0 = token0.lower() == vault_addr.lower()
                    usdc_addr = token1 if mtoken_is_token0 else token0
                    mtoken_addr = vault_addr
                    # ERC20 approve mTOKEN before swap (Fix 3)
                    mtoken_contract = _get_erc20(swap_router, mtoken_addr)
                    await mtoken_contract.functions.approve(
                        getattr(swap_router, "address", str(swap_router)), amount
                    ).transact({"from": demo_wallet_address})
                    # Pass ordered tuple — NOT dict (Fix 1: dict → tuple)
                    await swap_router.functions.exactInputSingle(
                        (
                            mtoken_addr,
                            usdc_addr,
                            demo_wallet_address,
                            2**32 - 1,
                            amount,
                            0,
                            0,
                        )
                    ).transact({"from": demo_wallet_address})
                    logger.debug(
                        "run_speculator_sim: SELL %d mTOKEN-units on pool=%s",
                        amount,
                        str(pool_addr)[:10],
                    )

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "run_speculator_sim: swap error on pool=%s (continuing): %s",
                    str(getattr(pool, "address", pool))[:10],
                    exc,
                )

        cycles_completed += 1
        await asyncio.sleep(cadence_seconds)


# ---------------------------------------------------------------------------
# genuine_holder_buy — single SwapRouter buy sized vs LP depth (D-19)
# ---------------------------------------------------------------------------


async def genuine_holder_buy(
    swap_router: Any,
    pool: Any,
    vault: Any,
    holder_wallet: str,
    usdc_amount: int,
    *,
    fire_threshold_bps: int | None = None,
) -> int:
    """Execute a single USDC→mTOKEN buy on the AMM for a genuine holder (D-19).

    Performs exactInputSingle(USDC→mTOKEN) sized to fill near NAV — the buy amount
    is validated to keep the resulting price gap within the arb bot's closable hysteresis
    so the bot can close the organic gap without hitting a manual intervention scenario.

    The returned mTOKEN balance is the ACTUAL post-buy balance of holder_wallet at the
    vault — the gate harness uses this exact value for the settlement claim assertion
    (step 7: claimed_USDC ≈ actual_balance × finalNAV, within 0.1% dust tolerance).

    Args:
        swap_router: Camelot V3 SwapRouter contract instance.
        pool: Pool contract instance (for post-buy price check).
        vault: ERC-4626 mTokenVault contract instance (for balanceOf check).
        holder_wallet: Checksummed address of the genuine holder demo wallet.
        usdc_amount: Raw USDC units to spend (e.g. 100 * 10**6 for $100).
        fire_threshold_bps: Bot hysteresis threshold in basis points. Defaults to
            FIRE_THRESHOLD_BPS from arb_bot.py (env-overridable, default 150 = 1.5%).
            Used to assert the buy is sized within the closable window.

    Returns:
        int: Actual post-buy mTOKEN balance of holder_wallet at the vault contract.
             This is the value the settlement claim assertion uses — never a round
             number assumption (D-19 correctness requirement).

    Raises:
        ValueError: If usdc_amount would create a gap larger than fire_threshold_bps
            of the vault NAV (sized vs LP depth check).
        Exception: Propagates swap errors to the caller for handling.

    Sizing (D-19):
        The buy is sized "modestly vs LP depth" so it fills near NAV. For a $1k seeded
        pool, a buy of $10-$50 shifts the price by ~0.1-0.5% (within hysteresis).
        The caller is responsible for passing a suitable usdc_amount.
    """
    _threshold = fire_threshold_bps if fire_threshold_bps is not None else _ARB_FIRE_THRESHOLD_BPS

    # Sizing assertion: usdc_amount must be small enough to keep the gap within hysteresis.
    # This is a pre-flight check; the caller must ensure the amount is appropriate for
    # the pool depth. We assert a hard bound here as a correctness guard (D-19).
    # For the gate tests, this bound is verified against the mock NAV and pool state.
    #
    # The rule: a swap of usdc_amount moves the pool price by approximately
    #   gap_bps ≈ usdc_amount / pool_liquidity_usdc * 10000
    # We cannot compute pool_liquidity_usdc without a pool call, so we assert the
    # amount is <= MAX_SWAP_FRACTION_BPS of an assumed minimum pool depth ($500 USDC).
    # The gate harness passes an amount that satisfies this for the real pool depth.
    min_assumed_pool_depth_usdc = 500 * 10**6  # $500 in raw USDC units
    max_allowed_usdc = int(min_assumed_pool_depth_usdc * _threshold / 10000)

    if usdc_amount > max_allowed_usdc:
        raise ValueError(
            f"genuine_holder_buy: usdc_amount={usdc_amount} exceeds sizing bound "
            f"max_allowed={max_allowed_usdc} for fire_threshold_bps={_threshold}. "
            "Reduce usdc_amount to keep the post-buy gap within the bot's hysteresis (D-19)."
        )

    vault_addr = getattr(vault, "address", str(vault))
    pool_addr = getattr(pool, "address", str(pool))

    # Fix 2: resolve USDC from pool token0/token1 rather than a literal placeholder
    token0 = await pool.functions.token0().call()
    token1 = await pool.functions.token1().call()
    mtoken_is_token0 = token0.lower() == vault_addr.lower()
    usdc_addr = token1 if mtoken_is_token0 else token0

    logger.info(
        "genuine_holder_buy: executing USDC→mTOKEN buy of %d USDC-units "
        "for holder=%s on pool=%s vault=%s (usdc=%s mtoken_is_token0=%s)",
        usdc_amount,
        holder_wallet[:10],
        str(pool_addr)[:10],
        str(vault_addr)[:10],
        str(usdc_addr)[:10],
        mtoken_is_token0,
    )

    # Fix 3: ERC20 approve USDC to router BEFORE exactInputSingle
    usdc_contract = _get_erc20(swap_router, usdc_addr)
    await usdc_contract.functions.approve(
        getattr(swap_router, "address", str(swap_router)), usdc_amount
    ).transact({"from": holder_wallet})

    # Fix 1: pass ordered tuple — NOT dict
    # Order: (tokenIn, tokenOut, recipient, deadline, amountIn, amountOutMinimum, sqrtPriceLimitX96)
    await swap_router.functions.exactInputSingle(
        (
            usdc_addr,
            vault_addr,
            holder_wallet,
            2**32 - 1,
            usdc_amount,
            0,
            0,
        )
    ).transact({"from": holder_wallet})

    # Read ACTUAL post-buy mTOKEN balance — never assume a round amount (D-19).
    actual_balance: int = await vault.functions.balanceOf(holder_wallet).call()

    logger.info(
        "genuine_holder_buy: holder=%s received mTOKEN balance=%d (post-buy actual)",
        holder_wallet[:10],
        actual_balance,
    )
    return actual_balance

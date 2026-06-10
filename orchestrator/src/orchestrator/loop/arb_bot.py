"""
House-arb bot — monitors 3 pools, fires arbCloseGap on key #4 (D-08/D-09/D-10).

Single process, sequential per-pool firing: prevents key #4 nonce self-contention
by construction (D-10). Key #4 is ARB-ONLY — it is NEVER shared with the
orchestrator-trade EOA.

Design invariants (D-08/D-09/D-10):
  - ARB_POLL_INTERVAL: 12s default (D-09: 10-15s range, env-overridable)
  - FIRE_THRESHOLD_BPS: 250 (2.5%) default hysteresis above the 1% contract floor
    (D-09; env-overridable; probe-justified floor above max Algebra Integral v1
    dynamic fee 1.49% per D-05/Probe 1 — reconciled in 04-08 Task 1)
  - CONTRACT_FLOOR_BPS: 100 (1% — the on-chain ArbitragePrimitive.GAP_THRESHOLD_BPS,
    documented here for reference; do NOT lower FIRE_THRESHOLD_BPS below this)
  - MAINNET_HOOK_PLACEHOLDER: None — D-09 Phase-6 extension point for gas/profit
    check before firing on mainnet. Set to a callable(gap_bps) -> bool to activate.
  - Per-pool fault isolation: exception in one pool → log ERROR + continue to next
    pool in the same tick (D-10)
  - CB-pause handling (D-07/Pitfall 6): arbMint reverts "Vault: mint paused" when the
    circuit breaker is active (AMM>NAV direction only). This is EXPECTED — log INFO,
    not ERROR; do NOT send an alert.
  - Key #4 depletion (Pitfall 4): preflight_key4_balance alerts if USDC working
    capital is below threshold at startup — silent depletion would disable gap-closing.
  - Close-time logging: gap_log_callback({gap_bps, close_time_s, tx}) enables the
    <60s budget tracking (D-08 criterion #2).

Reuses NonceManager (04-05) for key #4 nonce management.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from orchestrator.alerts.sink import AlertSeverity, send_alert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (D-09 — all env-overridable for test/demo/mainnet)
# ---------------------------------------------------------------------------

ARB_POLL_INTERVAL: int = int(os.environ.get("ARB_POLL_INTERVAL", "12"))
"""Poll cadence in seconds. D-09: 10-15s. Default 12s."""

FIRE_THRESHOLD_BPS: int = int(os.environ.get("FIRE_THRESHOLD_BPS", "250"))
"""Hysteresis floor in basis points. D-09: 2.5%. Must exceed CONTRACT_FLOOR_BPS.
Probe 1 (04-PROBE-RESULTS.md): Algebra Integral v1 max dynamic fee = 1.49%
(alpha1+alpha2=14900 bps); 250 bps (2.5%) is the probe-justified floor above that fee."""

CONTRACT_FLOOR_BPS: int = 100
"""On-chain ArbitragePrimitive.GAP_THRESHOLD_BPS = 1%. Documented here for reference.
arbCloseGap reverts 'AP: gap below threshold' if the gap is below this floor."""

MAINNET_HOOK_PLACEHOLDER: Callable[[int], bool] | None = None
"""D-09 Phase-6 extension point. When set to a callable(gap_bps: int) -> bool,
the bot calls it before firing; if it returns False, the gap is skipped.
Example use: mainnet gas/profit check. Sepolia: leave None (fire every qualifying gap)."""

# Key #4 USDC working-capital depletion alert threshold (Pitfall 4).
# Send WARNING if balance < this value (500 USDC in 1e6 units).
KEY4_USDC_MIN_WARNING: int = int(os.environ.get("KEY4_USDC_MIN_WARNING", str(500 * 10**6)))

# ---------------------------------------------------------------------------
# decode_pool_price_e18 — V3 sqrtPriceX96 → e18-scaled price (Section A2)
# ---------------------------------------------------------------------------


def decode_pool_price_e18(
    sqrt_price_x96: int,
    token0_decimals: int,
    token1_decimals: int,
    *,
    mtoken_is_token0: bool = True,
) -> int:
    """Convert Algebra V3 globalState().price (sqrtPriceX96) to USD price in 1e18 scale.

    The Algebra Integral v1 pool encodes the price of token1 in token0 terms as
    sqrtPriceX96 (Q64.96, same encoding as Uniswap V3 slot0.sqrtPriceX96).

    For a mTOKEN/USDC pair the token ordering depends on address sort order:
      - mtoken_is_token0=True  (mTOKEN < USDC address): price = USDC per mTOKEN
      - mtoken_is_token0=False (USDC < mTOKEN address): price = mTOKEN per USDC → invert

    Returns:
        AMM price of 1 mTOKEN in USDC terms, scaled to 1e18 (matching vault.nav()).

    Math reference: RESEARCH.md § A2 (Python equivalent).
    """
    if sqrt_price_x96 == 0:
        return 0

    if mtoken_is_token0:
        # token0=mTOKEN(18 dec), token1=USDC(6 dec)
        # raw_price = sqrtP^2 / 2^192 = price of token1 per token0 (USDC units per mTOKEN unit)
        # decimal_adj = 10^(token0_dec - token1_dec) = 10^12 (to cancel the decimal mismatch)
        # price_e18 = raw_price * decimal_adj * 1e18 (re-scale to e18)
        # Simplified: price_e18 = (sqrtP^2 * 10^(token0_dec-token1_dec)) / 2^192 * 1e18
        price_raw_num = sqrt_price_x96 * sqrt_price_x96
        price_raw_den = 2**192
        decimal_adj = 10 ** (token0_decimals - token1_decimals)
        # price in token1/token0 raw units * decimal_adj = price of 1 mTOKEN in USDC face-value
        # multiply by 1e18 to express in e18 scale
        price_e18 = price_raw_num * decimal_adj * 10**18 // price_raw_den
    else:
        # token0=USDC(6 dec), token1=mTOKEN(18 dec)
        # raw_price = sqrtP^2 / 2^192 = price of mTOKEN per USDC
        # We want price of USDC per mTOKEN → invert: price_mtoken_per_usdc → 1 / price
        # price_usdc_per_mtoken_e18 = 2^192 / sqrtP^2 * 10^(token1_dec - token0_dec) * 1e18
        price_raw_num = 2**192
        price_raw_den = sqrt_price_x96 * sqrt_price_x96
        if price_raw_den == 0:
            return 0
        decimal_adj = 10 ** (token1_decimals - token0_decimals)
        price_e18 = price_raw_num * decimal_adj * 10**18 // price_raw_den

    return price_e18


# ---------------------------------------------------------------------------
# read_sqrt_price_x96 — raw eth_call globalState (8-slot safe, item 4)
# ---------------------------------------------------------------------------

# Algebra Integral v1 globalState() 4-byte selector (keccak256("globalState()"))
_GLOBAL_STATE_SELECTOR: bytes = bytes.fromhex("e76c01e4")


async def read_sqrt_price_x96(web3: Any, pool_address: str) -> int:
    """Read Algebra pool sqrtPriceX96 via raw eth_call, taking only the first 32 bytes.

    Algebra Integral v1 globalState() returns a non-standard 256-byte / 8-slot layout
    that may not ABI-decode cleanly against the 6-output tuple declared in the inline ABI
    (VENUE-DECISION finding #1).  A raw call is unconditionally safe: slot 0 of the
    return data is ALWAYS the sqrtPriceX96 (uint160) regardless of how many extra slots
    the pool returns.

    Args:
        web3: AsyncWeb3 instance.
        pool_address: Checksummed Algebra pool address.

    Returns:
        sqrtPriceX96 as a Python int (extracted from the first 32 bytes of the return data).

    Raises:
        ValueError: If the return data is shorter than 32 bytes (pool not initialized or
            wrong address).
    """
    raw: bytes = await web3.eth.call({"to": pool_address, "data": _GLOBAL_STATE_SELECTOR})
    if len(raw) < 32:
        raise ValueError(
            f"read_sqrt_price_x96: pool={pool_address[:10]} returned {len(raw)} bytes "
            f"(expected ≥32); pool may not be initialized or the address is wrong"
        )
    sqrt_price_x96: int = int.from_bytes(raw[:32], "big")
    return sqrt_price_x96


async def detect_mtoken_is_token0(pool: Any, vault_address: str) -> bool:
    """Detect whether mTOKEN is token0 of the pool by reading pool.token0().

    Compares the lowercased checksummed addresses.  The non-mTOKEN token is USDC.

    Args:
        pool: Algebra pool contract instance (must expose token0() view method).
        vault_address: Checksummed mTOKEN vault address.

    Returns:
        True  if vault_address == pool.token0() (mTOKEN is token0, USDC is token1).
        False if vault_address == pool.token1() (USDC is token0, mTOKEN is token1).
    """
    token0: str = await pool.functions.token0().call()
    return token0.lower() == vault_address.lower()


# ---------------------------------------------------------------------------
# preflight_key4_balance — Pitfall 4: alert on USDC depletion
# ---------------------------------------------------------------------------


async def preflight_key4_balance(
    web3: Any,
    usdc_contract: Any,
    key4_address: str,
    *,
    min_usdc: int = KEY4_USDC_MIN_WARNING,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> int:
    """Read key #4 USDC balance; alert if below threshold (Pitfall 4).

    Depleted USDC working capital silently disables gap-closing (the arbMint leg
    requires USDC). This check runs at bot startup so the operator is alerted before
    the first poll cycle.

    Args:
        web3: AsyncWeb3 instance.
        usdc_contract: MockERC20 / USDC ERC-20 contract instance.
        key4_address: Checksummed address of arb bot key #4.
        min_usdc: Alert threshold in raw USDC units (default 500e6 = $500).
        telegram_bot_token: Optional Telegram bot token for alert delivery.
        telegram_chat_id: Optional Telegram chat ID.

    Returns:
        Current USDC balance of key #4 in raw units.
    """
    balance: int = await usdc_contract.functions.balanceOf(key4_address).call()
    logger.info(
        "preflight_key4_balance: key4=%s usdc_balance=%d min_threshold=%d",
        key4_address[:10],
        balance,
        min_usdc,
    )

    if balance < min_usdc:
        msg = (
            f"Key #4 USDC balance {balance / 1e6:.2f} USDC is below the "
            f"{min_usdc / 1e6:.0f} USDC threshold — arbCloseGap may fail on "
            f"the arbMint leg. Refund key4={key4_address[:10]}…"
        )
        logger.warning("Pitfall 4 — %s", msg)
        await send_alert(
            msg,
            AlertSeverity.WARNING,
            context={"key4": key4_address, "balance_usdc": balance, "min_usdc": min_usdc},
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )

    return balance


# ---------------------------------------------------------------------------
# arb_poll_loop — main peg-keeper loop (D-08/D-09/D-10)
# ---------------------------------------------------------------------------


async def arb_poll_loop(
    web3: Any,
    arb_primitive: Any,
    vault_pool_pairs: list[tuple[Any, Any]],
    arb_nonce_mgr: Any,
    *,
    key4_address: str,
    gap_log_callback: Callable[[dict], None] | None = None,
    stop_event: asyncio.Event | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """House-arb peg-keeper: polls all pools sequentially, fires arbCloseGap on key #4.

    Single-process, sequential per-pool firing — prevents key #4 nonce self-contention
    by construction (D-10). Each pool is wrapped in its own try/except for fault
    isolation: a failed arbCloseGap on one pool logs and continues without stopping
    the other two (D-10 per-pool fault isolation).

    CB-pause handling (D-07/Pitfall 6): when the circuit breaker is active and the AMM
    price is ABOVE NAV, arbMint reverts with "Vault: mint paused". This is EXPECTED
    during a CB episode — log at INFO level (event_type="expected_cb_pause"), do NOT
    send an alert.

    Gap close-time is logged and passed to gap_log_callback for the <60s budget
    tracking (D-08 criterion #2).

    Args:
        web3: AsyncWeb3 instance (used for wait_for_transaction_receipt).
        arb_primitive: ArbitragePrimitive contract instance.
        vault_pool_pairs: List of (vault_contract, pool_contract) tuples — one per model.
        arb_nonce_mgr: NonceManager bound to key #4 (arb-only EOA, D-10).
        key4_address: Checksummed address of arb bot key #4 (used in transact from=).
        gap_log_callback: Optional callable(dict) for close-time metrics logging.
            Called with {"gap_bps": int, "close_time_s": float, "tx": str}.
        stop_event: When set, the loop exits cleanly after the current tick completes.
        telegram_bot_token: Optional Telegram bot token for alert delivery.
        telegram_chat_id: Optional Telegram chat ID.
    """
    logger.info(
        "arb_poll_loop starting: pools=%d, poll_interval=%ds, threshold=%dbps, key4=%s",
        len(vault_pool_pairs),
        ARB_POLL_INTERVAL,
        FIRE_THRESHOLD_BPS,
        key4_address[:10],
    )

    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("arb_poll_loop: stop_event set, exiting cleanly")
            return

        for vault, pool in vault_pool_pairs:
            try:
                # ── 1. Read vault NAV ─────────────────────────────────────────
                nav_e18: int = await vault.functions.nav().call()
                if nav_e18 == 0:
                    logger.warning(
                        "arb_poll_loop: vault=%s nav=0, skipping this pool",
                        getattr(vault, "address", "?")[:10],
                    )
                    continue

                # ── 2. Read AMM price from Algebra globalState() via raw eth_call ─
                # Algebra Integral v1 globalState() returns a non-standard 8-slot
                # layout (VENUE-DECISION finding #1).  read_sqrt_price_x96 issues a
                # raw eth_call and extracts only the first 32 bytes (sqrtPriceX96)
                # without ABI-decoding the full return, avoiding the mismatch.
                pool_address_str: str = getattr(pool, "address", str(pool))
                sqrt_price_x96: int = await read_sqrt_price_x96(web3, pool_address_str)

                # ── 3. Detect token ordering (item 5) ────────────────────────────
                # mtoken_is_token0 is determined by reading pool.token0() and
                # comparing to the vault address (mTOKEN address).  This replaces
                # the previous hardcoded mtoken_is_token0=True.
                vault_address_str: str = getattr(vault, "address", str(vault))
                mtoken_is_token0: bool = await detect_mtoken_is_token0(pool, vault_address_str)

                amm_price_e18: int = decode_pool_price_e18(
                    sqrt_price_x96,
                    token0_decimals=18,  # mTOKEN = 18 dec
                    token1_decimals=6,  # USDC = 6 dec
                    mtoken_is_token0=mtoken_is_token0,
                )

                # ── 4. Compute gap ────────────────────────────────────────────
                gap_bps: int = abs(nav_e18 - amm_price_e18) * 10_000 // nav_e18

                logger.debug(
                    "arb_poll_loop: vault=%s nav_e18=%d amm_e18=%d gap_bps=%d threshold=%d",
                    getattr(vault, "address", "?")[:10],
                    nav_e18,
                    amm_price_e18,
                    gap_bps,
                    FIRE_THRESHOLD_BPS,
                )

                # ── 5. Hysteresis check ───────────────────────────────────────
                if gap_bps < FIRE_THRESHOLD_BPS:
                    continue  # Gap below hysteresis — do not fire

                # ── 6. Phase-6 mainnet economic hook (extension point, D-09) ──
                if MAINNET_HOOK_PLACEHOLDER is not None and not MAINNET_HOOK_PLACEHOLDER(gap_bps):
                    logger.info(
                        "arb_poll_loop: mainnet hook vetoed fire (gap_bps=%d, vault=%s)",
                        gap_bps,
                        getattr(vault, "address", "?")[:10],
                    )
                    continue

                # ── 7. Fire arbCloseGap on key #4 ────────────────────────────
                # vault_address_str / pool_address_str already resolved above (item 5)
                vault_address = vault_address_str
                pool_address = pool_address_str
                t0 = time.monotonic()

                tx_hash = await arb_nonce_mgr.assign_and_sign(
                    lambda nonce, _va=vault_address, _pa=pool_address: (
                        arb_primitive.functions.arbCloseGap(_va, _pa).transact(
                            {"from": key4_address, "nonce": nonce, "gas": 300_000}
                        )
                    )
                )

                receipt = await web3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
                elapsed = time.monotonic() - t0

                tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
                logger.info(
                    "arb_poll_loop: arbCloseGap closed %dbps gap in %.1fs "
                    "(tx=%s, status=%s, vault=%s)",
                    gap_bps,
                    elapsed,
                    tx_hex[:12],
                    getattr(receipt, "status", "?"),
                    vault_address[:10],
                )

                if gap_log_callback is not None:
                    gap_log_callback({"gap_bps": gap_bps, "close_time_s": elapsed, "tx": tx_hex})

            except Exception as exc:  # noqa: BLE001
                exc_str = str(exc).lower()

                # ── CB-pause expected (D-07 / Pitfall 6) ─────────────────────
                # When the circuit breaker is active, AMM>NAV arbMint reverts with
                # "Vault: mint paused". This is EXPECTED — do NOT alert. Log INFO.
                if "mint paused" in exc_str or "vault: mint paused" in exc_str:
                    logger.info(
                        "arb_poll_loop: expected_cb_pause on vault=%s "
                        "(arbMint blocked, circuit breaker active — not alert-worthy)",
                        getattr(vault, "address", "?")[:10],
                    )
                    # Per-pool fault isolation: continue to next pool
                    continue

                # ── Other error: log ERROR + continue (D-10 fault isolation) ──
                vault_addr_short = getattr(vault, "address", "unknown")[:10]
                pool_addr_short = getattr(pool, "address", "unknown")[:10]
                logger.error(
                    "arb_poll_loop: error on vault=%s/pool=%s: %s — "
                    "continuing to next pool (per-pool fault isolation D-10)",
                    vault_addr_short,
                    pool_addr_short,
                    exc,
                )
                # Per-pool fault isolation: continue to next pool (no re-raise)

        # ── End of pool loop — sleep until next poll ──────────────────────────
        if stop_event is not None and stop_event.is_set():
            logger.info("arb_poll_loop: stop_event set after tick, exiting cleanly")
            return

        await asyncio.sleep(ARB_POLL_INTERVAL)

"""Tests for orchestrator.loop.arb_bot (D-08/D-09/D-10 house-arb bot).

Tests mock all web3/contract interactions — no live chain, no broadcast.

Five behavior tests:
  1. test_arb_bot_fires_on_gap_above_hysteresis — fires on 1.6% gap, skips on 1.2%
  2. test_arb_bot_per_pool_fault_isolation — pool 1 raises, pools 2+3 still processed
  3. test_cb_pause_is_expected_not_error — mint-paused revert → INFO only, no alert
  4. test_key4_usdc_depletion_alerts — balance < threshold → send_alert WARNING
  5. test_close_time_logged — successful close logs gap_log_callback with required fields
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.loop.arb_bot import (
    CONTRACT_FLOOR_BPS,
    FIRE_THRESHOLD_BPS,
    MAINNET_HOOK_PLACEHOLDER,
    arb_poll_loop,
    preflight_key4_balance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault(nav_e18: int = 10**18) -> MagicMock:
    """Return a mock vault contract whose nav() returns nav_e18."""
    vault = MagicMock()
    vault.address = "0xAAAA000000000000000000000000000000000001"
    vault.functions.nav.return_value.call = AsyncMock(return_value=nav_e18)
    return vault


def _make_pool(sqrt_price_x96: int) -> MagicMock:
    """Return a mock Algebra pool whose globalState() returns [sqrt_price_x96, ...]."""
    pool = MagicMock()
    pool.address = "0xBBBB000000000000000000000000000000000002"
    # globalState returns tuple: (price, tick, lastFee, pluginConfig, communityFee, unlocked)
    pool.functions.globalState.return_value.call = AsyncMock(
        return_value=[sqrt_price_x96, 0, 0, 0, 0, True]
    )
    return pool


def _nav_e18_to_sqrt_price_x96(nav_price_ratio: float) -> int:
    """Convert a NAV-relative price (e.g. 1.016 = 1.6% above NAV) to sqrtPriceX96.

    Uses the mtoken_is_token0=True formula from decode_pool_price_e18 (reversed):
      price_e18 = sqrt^2 * 1e12 * 1e18 / 2^192
      sqrt^2 = price_e18 * 2^192 / (1e12 * 1e18)
      sqrt = sqrt(price_e18 * 2^192 / 1e30)
    where price_e18 = nav_price_ratio * 1e18
    """
    import math

    price_e18 = int(nav_price_ratio * 10**18)
    # From decode formula: price_e18 = sqrt^2 * 10^12 * 10^18 / 2^192
    # sqrt^2 = price_e18 * 2^192 / 10^30
    sqrt_sq = price_e18 * (2**192) / 10**30
    sqrt_price_x96 = int(math.sqrt(sqrt_sq))
    return sqrt_price_x96


def _make_nonce_mgr(tx_hash: str = "0xdeadbeef") -> MagicMock:
    """Return a mock NonceManager whose assign_and_sign returns tx_hash."""
    nonce_mgr = MagicMock()
    mock_tx = MagicMock()
    mock_tx.hex.return_value = tx_hash
    nonce_mgr.assign_and_sign = AsyncMock(return_value=mock_tx)
    return nonce_mgr


def _make_arb_primitive() -> MagicMock:
    """Return a mock ArbitragePrimitive contract."""
    arb = MagicMock()
    arb.functions.arbCloseGap.return_value.transact = MagicMock(return_value="0xdeadbeef")
    return arb


def _make_web3(tx_status: int = 1) -> MagicMock:
    """Return a mock web3 whose wait_for_transaction_receipt returns a receipt."""
    web3 = MagicMock()
    receipt = MagicMock()
    receipt.status = tx_status
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=receipt)
    return web3


# ---------------------------------------------------------------------------
# Test 1: Fires above hysteresis, skips below
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arb_bot_fires_on_gap_above_hysteresis() -> None:
    """ArbBot fires arbCloseGap when gap > FIRE_THRESHOLD_BPS; skips when below.

    Behavior:
      - gap = 1.6% (> 1.5% FIRE_THRESHOLD_BPS=150) → arbCloseGap called once
      - gap = 1.2% (< 1.5%) → arbCloseGap NOT called
    """
    # --- ABOVE HYSTERESIS: 1.6% gap ---
    nav_e18 = 10**18  # NAV = $1.00
    price_ratio_above = 1.016  # 1.6% above NAV → gap_bps = 160 >= 150
    sqrt_above = _nav_e18_to_sqrt_price_x96(price_ratio_above)

    vault = _make_vault(nav_e18)
    pool = _make_pool(sqrt_above)
    nonce_mgr = _make_nonce_mgr()
    arb = _make_arb_primitive()
    web3 = _make_web3()
    stop = asyncio.Event()

    # Run one tick then stop
    tick_count = 0

    async def sleep_and_stop(_: float) -> None:
        nonlocal tick_count
        tick_count += 1
        stop.set()

    with patch("orchestrator.loop.arb_bot.asyncio.sleep", side_effect=sleep_and_stop):
        await arb_poll_loop(
            web3,
            arb,
            [(vault, pool)],
            nonce_mgr,
            key4_address="0xKEY4000000000000000000000000000000000004",
            stop_event=stop,
        )

    nonce_mgr.assign_and_sign.assert_called_once()
    assert tick_count == 1

    # --- BELOW HYSTERESIS: 1.2% gap ---
    price_ratio_below = 1.012  # 1.2% above NAV → gap_bps = 120 < 150
    sqrt_below = _nav_e18_to_sqrt_price_x96(price_ratio_below)

    vault2 = _make_vault(nav_e18)
    pool2 = _make_pool(sqrt_below)
    nonce_mgr2 = _make_nonce_mgr()
    stop2 = asyncio.Event()
    tick_count2 = 0

    async def sleep_and_stop2(_: float) -> None:
        nonlocal tick_count2
        tick_count2 += 1
        stop2.set()

    with patch("orchestrator.loop.arb_bot.asyncio.sleep", side_effect=sleep_and_stop2):
        await arb_poll_loop(
            _make_web3(),
            _make_arb_primitive(),
            [(vault2, pool2)],
            nonce_mgr2,
            key4_address="0xKEY4000000000000000000000000000000000004",
            stop_event=stop2,
        )

    nonce_mgr2.assign_and_sign.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Per-pool fault isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arb_bot_per_pool_fault_isolation() -> None:
    """Exception in pool 1 does NOT stop processing of pools 2 and 3.

    Behavior:
      - 3 vault/pool pairs, all with gaps above hysteresis
      - Pool 1: assign_and_sign raises RuntimeError
      - Pools 2 and 3: fire correctly (assign_and_sign called successfully)
    """
    nav_e18 = 10**18
    sqrt_above = _nav_e18_to_sqrt_price_x96(1.016)  # 1.6% gap

    def make_pair(i: int) -> tuple[MagicMock, MagicMock]:
        v = _make_vault(nav_e18)
        v.address = f"0xAAAA00000000000000000000000000000000000{i}"
        p = _make_pool(sqrt_above)
        p.address = f"0xBBBB00000000000000000000000000000000000{i}"
        return v, p

    v1, p1 = make_pair(1)
    v2, p2 = make_pair(2)
    v3, p3 = make_pair(3)

    web3 = _make_web3()
    arb = _make_arb_primitive()

    call_count = 0

    async def assign_and_sign_side_effect(builder_coro: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("pool 1 failure: simulated arbCloseGap revert")
        mock_tx = MagicMock()
        mock_tx.hex.return_value = f"0xdeadbeef{call_count}"
        return mock_tx

    nonce_mgr = MagicMock()
    nonce_mgr.assign_and_sign = AsyncMock(side_effect=assign_and_sign_side_effect)

    stop = asyncio.Event()

    async def sleep_and_stop(_: float) -> None:
        stop.set()

    with patch("orchestrator.loop.arb_bot.asyncio.sleep", side_effect=sleep_and_stop):
        await arb_poll_loop(
            web3,
            arb,
            [(v1, p1), (v2, p2), (v3, p3)],
            nonce_mgr,
            key4_address="0xKEY4000000000000000000000000000000000004",
            stop_event=stop,
        )

    # assign_and_sign called 3 times: once per pool (pool 1 raised, 2+3 succeeded)
    assert nonce_mgr.assign_and_sign.call_count == 3, (
        f"Expected 3 assign_and_sign calls (one per pool); got {nonce_mgr.assign_and_sign.call_count}"
    )


# ---------------------------------------------------------------------------
# Test 3: CB-pause classified as expected (INFO, no alert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cb_pause_is_expected_not_error() -> None:
    """arbMint 'Vault: mint paused' → INFO log; no WARNING/CRITICAL alert sent.

    Behavior:
      - assign_and_sign raises Exception("Vault: mint paused")
      - The exception must be caught and logged at INFO level (not ERROR)
      - send_alert must NOT be called with WARNING or CRITICAL
    """
    nav_e18 = 10**18
    sqrt_above = _nav_e18_to_sqrt_price_x96(1.016)

    vault = _make_vault(nav_e18)
    pool = _make_pool(sqrt_above)
    web3 = _make_web3()
    arb = _make_arb_primitive()

    nonce_mgr = MagicMock()
    nonce_mgr.assign_and_sign = AsyncMock(
        side_effect=Exception("Vault: mint paused — circuit breaker active")
    )

    stop = asyncio.Event()

    async def sleep_and_stop(_: float) -> None:
        stop.set()

    with (
        patch("orchestrator.loop.arb_bot.asyncio.sleep", side_effect=sleep_and_stop),
        patch("orchestrator.loop.arb_bot.send_alert", new_callable=AsyncMock) as mock_alert,
        patch("orchestrator.loop.arb_bot.logger") as mock_logger,
    ):
        await arb_poll_loop(
            web3,
            arb,
            [(vault, pool)],
            nonce_mgr,
            key4_address="0xKEY4000000000000000000000000000000000004",
            stop_event=stop,
        )

    # send_alert must NOT have been called (CB-pause is not alert-worthy)
    mock_alert.assert_not_called()

    # The CB-pause must be logged at INFO (not ERROR)
    info_calls = [str(c) for c in mock_logger.info.call_args_list]
    assert any("expected_cb_pause" in c for c in info_calls), (
        f"Expected 'expected_cb_pause' in logger.info calls; got: {info_calls}"
    )
    # logger.error must NOT be called for the CB-pause
    error_calls = [str(c) for c in mock_logger.error.call_args_list]
    assert not any("mint paused" in c.lower() for c in error_calls), (
        f"CB-pause must not reach logger.error; got error calls: {error_calls}"
    )


# ---------------------------------------------------------------------------
# Test 4: Key #4 USDC depletion sends WARNING alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key4_usdc_depletion_alerts() -> None:
    """preflight_key4_balance sends WARNING alert when balance < threshold.

    Behavior:
      - balance = 100 USDC (below 500 USDC threshold)
      - send_alert called with AlertSeverity.WARNING
      - balance = 1000 USDC (above threshold)
      - send_alert NOT called
    """
    from orchestrator.alerts.sink import AlertSeverity

    key4_addr = "0xKEY4000000000000000000000000000000000004"
    web3 = MagicMock()

    # --- Below threshold ---
    low_balance = 100 * 10**6  # 100 USDC
    usdc_low = MagicMock()
    usdc_low.functions.balanceOf.return_value.call = AsyncMock(return_value=low_balance)

    with patch("orchestrator.loop.arb_bot.send_alert", new_callable=AsyncMock) as mock_alert:
        balance = await preflight_key4_balance(web3, usdc_low, key4_addr, min_usdc=500 * 10**6)

    assert balance == low_balance
    mock_alert.assert_called_once()
    call_kwargs = mock_alert.call_args
    # Second positional arg is severity
    severity_arg = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("severity")
    assert severity_arg == AlertSeverity.WARNING, (
        f"Expected WARNING alert for depleted key4; got {severity_arg}"
    )

    # --- Above threshold ---
    high_balance = 1000 * 10**6  # 1000 USDC
    usdc_high = MagicMock()
    usdc_high.functions.balanceOf.return_value.call = AsyncMock(return_value=high_balance)

    with patch("orchestrator.loop.arb_bot.send_alert", new_callable=AsyncMock) as mock_alert2:
        balance2 = await preflight_key4_balance(web3, usdc_high, key4_addr, min_usdc=500 * 10**6)

    assert balance2 == high_balance
    mock_alert2.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: Close-time logged to gap_log_callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_time_logged() -> None:
    """Successful arbCloseGap fires gap_log_callback with {gap_bps, close_time_s, tx}.

    Behavior:
      - gap above hysteresis → arbCloseGap fires
      - gap_log_callback called with a dict containing gap_bps, close_time_s, tx
      - gap_bps: integer > 0
      - close_time_s: float >= 0
      - tx: str (hex string)
    """
    nav_e18 = 10**18
    sqrt_above = _nav_e18_to_sqrt_price_x96(1.016)

    vault = _make_vault(nav_e18)
    pool = _make_pool(sqrt_above)
    web3 = _make_web3()
    arb = _make_arb_primitive()
    nonce_mgr = _make_nonce_mgr(tx_hash="0xabcdef1234567890")

    logged: list[dict] = []

    def callback(entry: dict) -> None:
        logged.append(entry)

    stop = asyncio.Event()

    async def sleep_and_stop(_: float) -> None:
        stop.set()

    with patch("orchestrator.loop.arb_bot.asyncio.sleep", side_effect=sleep_and_stop):
        await arb_poll_loop(
            web3,
            arb,
            [(vault, pool)],
            nonce_mgr,
            key4_address="0xKEY4000000000000000000000000000000000004",
            gap_log_callback=callback,
            stop_event=stop,
        )

    assert len(logged) == 1, f"Expected 1 gap_log_callback call; got {len(logged)}"
    entry = logged[0]
    assert "gap_bps" in entry, f"gap_bps missing from callback entry: {entry}"
    assert "close_time_s" in entry, f"close_time_s missing from callback entry: {entry}"
    assert "tx" in entry, f"tx missing from callback entry: {entry}"
    assert isinstance(entry["gap_bps"], int) and entry["gap_bps"] > 0, (
        f"gap_bps must be positive int; got {entry['gap_bps']}"
    )
    assert isinstance(entry["close_time_s"], float) and entry["close_time_s"] >= 0, (
        f"close_time_s must be non-negative float; got {entry['close_time_s']}"
    )
    assert isinstance(entry["tx"], str), f"tx must be str; got {type(entry['tx'])}"


# ---------------------------------------------------------------------------
# Sanity: constants are correctly configured
# ---------------------------------------------------------------------------


def test_fire_threshold_above_contract_floor() -> None:
    """FIRE_THRESHOLD_BPS (1.5%) must be > CONTRACT_FLOOR_BPS (1%) by design."""
    assert FIRE_THRESHOLD_BPS > CONTRACT_FLOOR_BPS, (
        f"FIRE_THRESHOLD_BPS={FIRE_THRESHOLD_BPS} must be > CONTRACT_FLOOR_BPS={CONTRACT_FLOOR_BPS}"
    )


def test_mainnet_hook_placeholder_is_none_by_default() -> None:
    """MAINNET_HOOK_PLACEHOLDER defaults to None (Sepolia: fire every qualifying gap)."""
    assert MAINNET_HOOK_PLACEHOLDER is None, (
        f"MAINNET_HOOK_PLACEHOLDER should default to None; got {MAINNET_HOOK_PLACEHOLDER!r}"
    )

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
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.loop.arb_bot import (
    CONTRACT_FLOOR_BPS,
    FIRE_THRESHOLD_BPS,
    MAINNET_HOOK_PLACEHOLDER,
    arb_poll_loop,
    decode_pool_price_e18,
    detect_mtoken_is_token0,
    preflight_key4_balance,
    read_sqrt_price_x96,
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


def _make_pool(sqrt_price_x96: int, vault_address: str | None = None) -> MagicMock:
    """Return a mock Algebra pool whose token0() returns vault_address (mTOKEN=token0 default).

    arb_poll_loop now reads sqrtPriceX96 via raw web3.eth.call (item 4) rather than
    pool.functions.globalState().call().  The raw return is wired on web3 in _make_web3().
    pool.functions.token0() is used by detect_mtoken_is_token0 (item 5).
    """
    _vault_addr = vault_address or "0xAAAA000000000000000000000000000000000001"
    pool = MagicMock()
    pool.address = "0xBBBB000000000000000000000000000000000002"
    # token0 = vault address (mTOKEN is token0 by default in tests)
    pool.functions.token0.return_value.call = AsyncMock(return_value=_vault_addr)
    # Keep globalState stub for any legacy callers (not used by arb_poll_loop anymore)
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


def _make_web3(tx_status: int = 1, sqrt_price_x96: int | None = None) -> MagicMock:
    """Return a mock web3 with wait_for_transaction_receipt + eth.call for raw globalState.

    arb_poll_loop uses read_sqrt_price_x96 which issues:
      await web3.eth.call({"to": pool_address, "data": _GLOBAL_STATE_SELECTOR})
    and extracts the first 32 bytes as sqrtPriceX96.

    If sqrt_price_x96 is provided, web3.eth.call returns a 256-byte payload with the
    given sqrtPriceX96 in the first slot (mimicking Algebra Integral v1 8-slot return).
    If omitted, defaults to the at-peg sqrtPriceX96 (NAV=$1.00).
    """
    _sqrt = sqrt_price_x96 if sqrt_price_x96 is not None else 79228162514264337593543950336
    raw_return = _sqrt.to_bytes(32, "big") + b"\x00" * 224  # 8-slot layout, extras zeroed

    web3 = MagicMock()
    receipt = MagicMock()
    receipt.status = tx_status
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=receipt)
    web3.eth.call = AsyncMock(return_value=raw_return)
    return web3


# ---------------------------------------------------------------------------
# Test 1: Fires above hysteresis, skips below
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arb_bot_fires_on_gap_above_hysteresis() -> None:
    """ArbBot fires arbCloseGap when gap > FIRE_THRESHOLD_BPS; skips when below.

    Behavior (after Probe 1 reconciliation — 04-08 Task 1):
      - gap = 2.6% (> 2.5% FIRE_THRESHOLD_BPS=250) → arbCloseGap called once
      - gap = 1.6% (< 2.5%) → arbCloseGap NOT called
    FIRE_THRESHOLD_BPS=250 is the probe-justified floor above Algebra Integral v1's
    max dynamic fee of 1.49% (Probe 1: alpha1+alpha2=14900 bps).
    """
    # --- ABOVE HYSTERESIS: 2.6% gap ---
    nav_e18 = 10**18  # NAV = $1.00
    price_ratio_above = 1.026  # 2.6% above NAV → gap_bps = 260 >= 250
    sqrt_above = _nav_e18_to_sqrt_price_x96(price_ratio_above)

    vault = _make_vault(nav_e18)
    # pool token0 must match vault address for detect_mtoken_is_token0 (item 5)
    pool = _make_pool(sqrt_above, vault_address=vault.address)
    nonce_mgr = _make_nonce_mgr()
    arb = _make_arb_primitive()
    # web3.eth.call returns the raw globalState payload (item 4)
    web3 = _make_web3(sqrt_price_x96=sqrt_above)
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

    # --- BELOW HYSTERESIS: 1.6% gap ---
    price_ratio_below = 1.016  # 1.6% above NAV → gap_bps = 160 < 250
    sqrt_below = _nav_e18_to_sqrt_price_x96(price_ratio_below)

    vault2 = _make_vault(nav_e18)
    pool2 = _make_pool(sqrt_below, vault_address=vault2.address)
    nonce_mgr2 = _make_nonce_mgr()
    stop2 = asyncio.Event()
    tick_count2 = 0

    async def sleep_and_stop2(_: float) -> None:
        nonlocal tick_count2
        tick_count2 += 1
        stop2.set()

    with patch("orchestrator.loop.arb_bot.asyncio.sleep", side_effect=sleep_and_stop2):
        await arb_poll_loop(
            _make_web3(sqrt_price_x96=sqrt_below),
            _make_arb_primitive(),
            [(vault2, pool2)],
            nonce_mgr2,
            key4_address="0xKEY4000000000000000000000000000000000004",
            stop_event=stop2,
        )

    nonce_mgr2.assign_and_sign.assert_not_called()


# ---------------------------------------------------------------------------
# Test 1b: Case-B (USDC=token0) on-peg pool must NOT fire (04-GATE.md Seam C regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arb_bot_caseB_onpeg_pool_does_not_fire() -> None:
    """REGRESSION (04-GATE.md Seam C): a Case-B pool (USDC=token0) that is ON-PEG must NOT fire.

    The live gate fired arbCloseGap every tick on the Case-B Claude pool because arb_poll_loop
    hardcoded token0_decimals=18/token1_decimals=6 regardless of ordering. For Case B that makes
    decimal_adj = 10**(6-18) = 1e-12 → the decoded price floors to 0 → gap=10000bps → fire →
    on-chain revert 'AP: gap below threshold' (the pool is actually on-peg). Decimals MUST follow
    ordering (mirror preflight). This test FAILS on the old hardcoded code and passes after the fix.
    Every other arb_bot test uses Case A (token0=vault), which is exactly why the bug slipped CI.
    """
    nav_e18 = 10**18
    usdc_addr = "0x" + "11" * 20  # token0 = USDC (Case B: USDC < mTOKEN address)
    vault_addr = "0x" + "22" * 20  # mTOKEN / vault address (token1)

    # Case-B on-peg sqrtPriceX96: price_usdc_per_mtoken_e18 == 1e18  ⇒  sqrtP = 2^96 * 1e6.
    sqrt_caseB_onpeg = (2**96) * (10**6)

    # Sanity: ordering-aware decimals decode this as on-peg (~1e18); the old hardcoded 18/6 floored to 0.
    price_e18 = decode_pool_price_e18(
        sqrt_caseB_onpeg, token0_decimals=6, token1_decimals=18, mtoken_is_token0=False
    )
    assert abs(price_e18 - nav_e18) * 10_000 // nav_e18 <= 50, (
        f"Case-B on-peg decode off: {price_e18}"
    )

    vault = _make_vault(nav_e18)
    vault.address = vault_addr
    pool = _make_pool(sqrt_caseB_onpeg, vault_address=usdc_addr)  # token0() = USDC ⇒ Case B
    nonce_mgr = _make_nonce_mgr()
    arb = _make_arb_primitive()
    web3 = _make_web3(sqrt_price_x96=sqrt_caseB_onpeg)
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
            stop_event=stop,
        )

    # On-peg Case-B pool ⇒ gap ≈ 0 ⇒ must NOT fire (old code fired on a bogus 10000bps gap).
    nonce_mgr.assign_and_sign.assert_not_called()


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
    sqrt_above = _nav_e18_to_sqrt_price_x96(1.026)  # 2.6% gap (above 2.5% threshold)

    def make_pair(i: int) -> tuple[MagicMock, MagicMock]:
        v = _make_vault(nav_e18)
        v.address = f"0xAAAA00000000000000000000000000000000000{i}"
        # pool.token0 must match vault address (detect_mtoken_is_token0, item 5)
        p = _make_pool(sqrt_above, vault_address=v.address)
        p.address = f"0xBBBB00000000000000000000000000000000000{i}"
        return v, p

    v1, p1 = make_pair(1)
    v2, p2 = make_pair(2)
    v3, p3 = make_pair(3)

    # web3.eth.call provides raw globalState payload (item 4)
    web3 = _make_web3(sqrt_price_x96=sqrt_above)
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
    sqrt_above = _nav_e18_to_sqrt_price_x96(1.026)

    vault = _make_vault(nav_e18)
    pool = _make_pool(sqrt_above, vault_address=vault.address)
    web3 = _make_web3(sqrt_price_x96=sqrt_above)
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
    sqrt_above = _nav_e18_to_sqrt_price_x96(1.026)

    vault = _make_vault(nav_e18)
    pool = _make_pool(sqrt_above, vault_address=vault.address)
    web3 = _make_web3(sqrt_price_x96=sqrt_above)
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
    """FIRE_THRESHOLD_BPS (2.5%) must be > CONTRACT_FLOOR_BPS (1%) by design.

    Probe 1 (04-PROBE-RESULTS.md): max Algebra Integral v1 dynamic fee = 1.49%
    (alpha1+alpha2 = 14900 bps, baseFee = 0). The probe-justified floor is 2.5%
    (250 bps) to clear the fee band plus a slippage buffer — reconciled 04-08 Task 1.
    """
    assert FIRE_THRESHOLD_BPS > CONTRACT_FLOOR_BPS, (
        f"FIRE_THRESHOLD_BPS={FIRE_THRESHOLD_BPS} must be > CONTRACT_FLOOR_BPS={CONTRACT_FLOOR_BPS}"
    )
    assert FIRE_THRESHOLD_BPS == 250 or int(os.environ.get("FIRE_THRESHOLD_BPS", "0")) > 0, (
        f"Default FIRE_THRESHOLD_BPS should be 250 (probe-justified); got {FIRE_THRESHOLD_BPS}"
    )


def test_decode_pool_price_e18_ground_truth_anchor() -> None:
    """GROUND-TRUTH ANCHOR for the AMM price decode (regression guard for the 1e12-vs-1e30
    scaling bug fixed 2026-06-11). Derived from token decimals + the Q96 price encoding —
    NOT from the decode formula. A pool that is physically on-peg (1 mTOKEN = 1 USDC) must
    decode to exactly 1e18 in BOTH token orderings. The original suite encoded the buggy
    convention and passed anyway; this anchor cannot.

    On-peg sqrtPriceX96 (18-dec mTOKEN vs 6-dec USDC):
      Case A (mTOKEN=token0): price token1/token0 = 1e6/1e18 = 1e-12 -> sqrtP = Q96 / 1e6
      Case B (USDC=token0):   price token1/token0 = 1e18/1e6 = 1e12  -> sqrtP = 1e6 * Q96
    """
    Q96 = 2**96
    # Case B: USDC=token0(6dec), mTOKEN=token1(18dec)
    case_b = decode_pool_price_e18(
        10**6 * Q96, token0_decimals=6, token1_decimals=18, mtoken_is_token0=False
    )
    assert case_b == 10**18, f"Case B on-peg must decode to exactly 1e18, got {case_b}"
    # Case A: mTOKEN=token0(18dec), USDC=token1(6dec). floor(Q96/1e6) loses <1 wei of sqrtP.
    case_a = decode_pool_price_e18(
        Q96 // 10**6, token0_decimals=18, token1_decimals=6, mtoken_is_token0=True
    )
    assert abs(case_a - 10**18) <= 2, f"Case A on-peg must decode to ~1e18, got {case_a}"
    # The OLD buggy convention treated sqrtP = Q96/1000 (Case B) as on-peg. Under the correct
    # 1e30 factor it must NOT read on-peg — proving we are off the buggy convention for good.
    buggy = decode_pool_price_e18(
        Q96 // 1000, token0_decimals=6, token1_decimals=18, mtoken_is_token0=False
    )
    assert buggy != 10**18, (
        "buggy-convention sqrtP must NOT decode on-peg under the corrected factor"
    )


def test_mainnet_hook_placeholder_is_none_by_default() -> None:
    """MAINNET_HOOK_PLACEHOLDER defaults to None (Sepolia: fire every qualifying gap)."""
    assert MAINNET_HOOK_PLACEHOLDER is None, (
        f"MAINNET_HOOK_PLACEHOLDER should default to None; got {MAINNET_HOOK_PLACEHOLDER!r}"
    )


# ---------------------------------------------------------------------------
# Item 4: raw globalState decode — read_sqrt_price_x96
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_sqrt_price_x96_extracts_first_slot() -> None:
    """read_sqrt_price_x96 extracts sqrtPriceX96 from the first 32 bytes of raw return data.

    The Algebra Integral v1 pool may return 256 bytes (8 slots).  read_sqrt_price_x96 issues
    a raw eth_call and takes int.from_bytes(raw[:32], 'big') — the first slot is always
    sqrtPriceX96 regardless of extra slots returned.

    Test: craft a 256-byte return value where the first 32 bytes encode a known sqrtPriceX96
    and the remaining 224 bytes are filled with 0xFF (would corrupt ABI decode but not raw).
    Verify read_sqrt_price_x96 returns the expected value.
    """
    expected_sqrt_price = 79228162514264337593543950336  # sqrtPriceX96 at NAV=$1.00

    # Craft 256-byte raw return: first 32 bytes = expected_sqrt_price, rest = 0xFF
    first_slot = expected_sqrt_price.to_bytes(32, "big")
    filler = bytes([0xFF] * 224)  # 7 extra slots that would corrupt ABI decode
    raw_return = first_slot + filler
    assert len(raw_return) == 256

    # Mock web3.eth.call to return the crafted bytes
    mock_web3 = MagicMock()
    mock_web3.eth.call = AsyncMock(return_value=raw_return)

    pool_address = "0xBBBB000000000000000000000000000000000002"
    result = await read_sqrt_price_x96(mock_web3, pool_address)

    assert result == expected_sqrt_price, (
        f"read_sqrt_price_x96 should return {expected_sqrt_price}; got {result}"
    )
    # Verify the call was made with the correct selector
    mock_web3.eth.call.assert_called_once()
    call_kwargs = mock_web3.eth.call.call_args[0][0]  # first positional arg (dict)
    assert call_kwargs["to"] == pool_address
    assert call_kwargs["data"] == bytes.fromhex("e76c01e4"), (
        f"selector must be 0xe76c01e4 (globalState()); got {call_kwargs['data'].hex()}"
    )


@pytest.mark.asyncio
async def test_read_sqrt_price_x96_raises_on_short_return() -> None:
    """read_sqrt_price_x96 raises ValueError if fewer than 32 bytes are returned."""
    mock_web3 = MagicMock()
    mock_web3.eth.call = AsyncMock(return_value=b"\x00" * 16)  # only 16 bytes

    with pytest.raises(ValueError, match="returned 16 bytes"):
        await read_sqrt_price_x96(mock_web3, "0x" + "0" * 40)


# ---------------------------------------------------------------------------
# Item 5: token ordering detection — detect_mtoken_is_token0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_mtoken_is_token0_true() -> None:
    """detect_mtoken_is_token0 returns True when vault_address == pool.token0()."""
    vault_addr = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA01"

    pool = MagicMock()
    pool.functions.token0.return_value.call = AsyncMock(return_value=vault_addr)

    result = await detect_mtoken_is_token0(pool, vault_addr)
    assert result is True, f"mTOKEN=token0 case: expected True, got {result}"


@pytest.mark.asyncio
async def test_detect_mtoken_is_token0_false() -> None:
    """detect_mtoken_is_token0 returns False when vault_address == pool.token1() (not token0)."""
    vault_addr = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA01"
    usdc_addr = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB02"

    pool = MagicMock()
    # token0 is USDC, not vault
    pool.functions.token0.return_value.call = AsyncMock(return_value=usdc_addr)

    result = await detect_mtoken_is_token0(pool, vault_addr)
    assert result is False, f"mTOKEN=token1 case: expected False, got {result}"


@pytest.mark.asyncio
async def test_detect_mtoken_is_token0_case_insensitive() -> None:
    """detect_mtoken_is_token0 comparison is case-insensitive (checksummed vs lowercase)."""
    vault_addr_checksum = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA01"
    vault_addr_lower = vault_addr_checksum.lower()

    pool = MagicMock()
    # Pool returns lowercase; vault is passed as checksummed
    pool.functions.token0.return_value.call = AsyncMock(return_value=vault_addr_lower)

    result = await detect_mtoken_is_token0(pool, vault_addr_checksum)
    assert result is True, f"Case-insensitive comparison failed: expected True, got {result}"


@pytest.mark.asyncio
async def test_arb_bot_uses_raw_sqrt_price_and_detects_ordering() -> None:
    """arb_poll_loop calls read_sqrt_price_x96 (raw eth_call) and detect_mtoken_is_token0.

    Verify that arb_poll_loop routes through the new helpers rather than calling
    pool.functions.globalState() directly.  The pool mock does NOT have globalState()
    configured — any call to it would raise AttributeError and fail the test.
    """
    nav_e18 = 10**18
    sqrt_above = _nav_e18_to_sqrt_price_x96(1.026)  # 2.6% above NAV

    # Vault + pool mocks — pool has NO globalState configured (ensures it is not called)
    vault = _make_vault(nav_e18)
    vault_addr = vault.address

    pool = MagicMock()
    pool.address = "0xBBBB000000000000000000000000000000000002"
    # token0 = vault (mTOKEN is token0)
    pool.functions.token0.return_value.call = AsyncMock(return_value=vault_addr)
    # Deliberately do NOT wire pool.functions.globalState — raw call bypasses this

    # web3.eth.call returns the sqrt_price in the first 32 bytes (item 4)
    raw_return = sqrt_above.to_bytes(32, "big") + b"\x00" * 224
    web3 = MagicMock()
    web3.eth.call = AsyncMock(return_value=raw_return)
    receipt = MagicMock()
    receipt.status = 1
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=receipt)

    nonce_mgr = _make_nonce_mgr()
    arb = _make_arb_primitive()
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
            stop_event=stop,
        )

    # arbCloseGap must have been fired (gap was above threshold)
    nonce_mgr.assign_and_sign.assert_called_once()
    # Confirm web3.eth.call was invoked with globalState selector
    web3.eth.call.assert_called_once()
    call_data = web3.eth.call.call_args[0][0]
    assert call_data["data"] == bytes.fromhex("e76c01e4"), (
        "arb_poll_loop must call globalState via raw eth_call (selector 0xe76c01e4)"
    )

"""Unit tests for CR-02/WR-03 action dispatch fixes.

Regression tests:
  (a) close decision routes to closePosition; openLong/openShort NOT called.
  (b) close with no open position is rejected-and-journaled; no order created.
  (c) D-10 limit rejects a 2nd open on a market that already has an on-chain position.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.loop.driver import _build_open_positions
from orchestrator.schema import Decision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(**overrides) -> Decision:
    """Build a minimal valid Decision for testing."""
    defaults = {
        "action": "open",
        "market": "ETH",
        "side": "long",
        "sizeUsd": 1000.0,
        "leverage": 2.0,
        "rationale": "test trade",
        "confidence": 0.8,
        "expectedHoldingPeriod": "short",
    }
    defaults.update(overrides)
    return Decision.model_validate(defaults)


def _make_aggregators_mock():
    """Build aggregator mocks that behave correctly with read_mark_prices."""
    aggregators = {}
    for asset in ("ETH", "BTC", "SOL"):
        agg = MagicMock()
        # contract.functions.latestRoundData() returns an object with .call() coroutine
        round_data_call = AsyncMock(return_value=(1, int(3000 * 1e8), 0, 9999999999, 1))
        agg.functions.latestRoundData.return_value.call = round_data_call
        aggregators[asset] = agg
    return aggregators


# ---------------------------------------------------------------------------
# (a) close decision routes to closePosition; openLong/openShort NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_action_calls_close_position_not_open() -> None:
    """A close decision must call closePosition and NEVER openLong/openShort.

    CR-02 regression test.
    """
    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.failure_tracker import FailureTracker
    from orchestrator.loop.session import SessionConfig

    # Position already open for ETH
    pos_key_hex = "0x" + "ab" * 32
    open_positions = {
        "ETH": {
            "position_key": pos_key_hex,
            "side": "long",
            "size_usd": 1000.0,
        }
    }

    # The decision is a close for ETH
    close_decision = _make_decision(action="close", side=None, market="ETH")

    # Fake receipt with OrderCreated event (close path also emits OrderCreated).
    # Explicitly set status=1 (success) so the new receipt.get("status") == 0 guard passes.
    fake_order_key_bytes = bytes.fromhex("cd" * 32)
    fake_receipt = MagicMock()
    fake_receipt.get = MagicMock(return_value=1)  # status=1 (success, not revert)

    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=10)
    # GAP-1a fix: driver now uses wait_for_transaction_receipt (not get_transaction_receipt)
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=fake_receipt)

    mock_perps = MagicMock()
    mock_perps.functions.executionDelay.return_value.call = AsyncMock(return_value=1)

    # closePosition: returns a tx hash
    fake_close_tx = bytes.fromhex("de" * 32)
    mock_perps.functions.closePosition.return_value.transact = AsyncMock(return_value=fake_close_tx)

    # OrderCreated from closePosition
    mock_perps.events.OrderCreated.return_value.process_receipt = MagicMock(
        return_value=[{"args": {"orderKey": fake_order_key_bytes}}]
    )

    # openLong/openShort: if called, we want to detect it
    open_long_called = []
    open_short_called = []
    mock_perps.functions.openLong.side_effect = (
        lambda *a, **kw: open_long_called.append(a) or MagicMock()
    )
    mock_perps.functions.openShort.side_effect = (
        lambda *a, **kw: open_short_called.append(a) or MagicMock()
    )

    db = AsyncMock()
    config = SessionConfig(
        session_id="00000000-0000-0000-0000-000000000001",
        session_key="test-session",
        session_duration_seconds=60,
        cadence_seconds=1.0,
        price_seed=42,
    )
    tracker = FailureTracker()

    walk = MagicMock()
    walk.funding_rate = MagicMock(return_value=0.0001)
    walk.change_24h = MagicMock(return_value=0.01)

    aggregators = _make_aggregators_mock()

    with (
        patch("orchestrator.loop.driver.call_claude") as mock_call_claude,
        patch("orchestrator.loop.driver.extract_tool_input") as mock_extract,
        patch("orchestrator.loop.driver.validate_decision") as mock_validate,
        patch("orchestrator.loop.driver.record_journal_pending", new_callable=AsyncMock),
        patch("orchestrator.loop.driver.record_pending_order", new_callable=AsyncMock),
        patch("orchestrator.loop.driver.mark_pending_order_reconciled", new_callable=AsyncMock),
        patch("orchestrator.loop.driver.record_model_status", new_callable=AsyncMock),
        patch("orchestrator.loop.driver.validate_business_rules", return_value=None),
        patch("backend.ws.channels.channel_for", return_value="test-channel"),
        patch("orchestrator.loop.driver._publish", new_callable=AsyncMock),
    ):
        mock_call_claude.return_value = MagicMock()
        mock_extract.return_value = {"action": "close", "market": "ETH"}
        mock_validate.return_value = close_decision

        result = await run_live_cycle(
            web3,
            mock_perps,
            "0xVault",
            "claude-opus-4-7",
            1,
            config=config,
            walk=walk,
            aggregators=aggregators,
            tracker=tracker,
            db=db,
            redis=None,
            session_id=config.session_id,
            seq=1,
            available_usdc=10000.0,
            open_positions=open_positions,
            nav_table="NAV",
            positions_table="ETH long",
            recent_decisions="None",
            elapsed_seconds=0.0,
        )

    # closePosition must have been called
    assert mock_perps.functions.closePosition.called, (
        "closePosition was not called for a close decision"
    )
    # openLong and openShort must NOT have been called
    assert not open_long_called, "openLong was called for a close decision — CR-02 regression"
    assert not open_short_called, "openShort was called for a close decision — CR-02 regression"
    # Result should be 'submitted' (close order went through)
    assert result["status"] == "submitted", f"Expected submitted, got: {result}"


# ---------------------------------------------------------------------------
# (b) close with no open position is rejected-and-journaled; no order created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_with_no_open_position_is_rejected() -> None:
    """A close decision with no open position must be rejected, no order created.

    CR-02 regression test.
    """
    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.failure_tracker import FailureTracker
    from orchestrator.loop.session import SessionConfig

    # No open positions for ETH
    open_positions: dict = {}

    close_decision = _make_decision(action="close", side=None, market="ETH")

    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=10)

    mock_perps = MagicMock()
    mock_perps.functions.executionDelay.return_value.call = AsyncMock(return_value=1)

    close_called = []
    mock_perps.functions.closePosition.side_effect = (
        lambda *a, **kw: close_called.append(True) or MagicMock()
    )

    db = AsyncMock()
    config = SessionConfig(
        session_id="00000000-0000-0000-0000-000000000001",
        session_key="test-session",
        session_duration_seconds=60,
        cadence_seconds=1.0,
        price_seed=42,
    )
    tracker = FailureTracker()

    walk = MagicMock()
    walk.funding_rate = MagicMock(return_value=0.0)
    walk.change_24h = MagicMock(return_value=0.0)

    aggregators = _make_aggregators_mock()

    journal_calls = []

    async def fake_journal(db, *, vault_address, order_key, **kw):
        journal_calls.append(order_key)

    with (
        patch("orchestrator.loop.driver.call_claude") as mock_call_claude,
        patch("orchestrator.loop.driver.extract_tool_input") as mock_extract,
        patch("orchestrator.loop.driver.validate_decision") as mock_validate,
        patch("orchestrator.loop.driver.record_journal_pending", side_effect=fake_journal),
        patch("orchestrator.loop.driver.record_pending_order", new_callable=AsyncMock),
        patch("orchestrator.loop.driver.mark_pending_order_reconciled", new_callable=AsyncMock),
        patch("orchestrator.loop.driver.record_model_status", new_callable=AsyncMock),
        patch("orchestrator.loop.driver.validate_business_rules", return_value=None),
        patch("backend.ws.channels.channel_for", return_value="test-channel"),
        patch("orchestrator.loop.driver._publish", new_callable=AsyncMock),
    ):
        mock_call_claude.return_value = MagicMock()
        mock_extract.return_value = {"action": "close", "market": "ETH"}
        mock_validate.return_value = close_decision

        result = await run_live_cycle(
            web3,
            mock_perps,
            "0xVault",
            "claude-opus-4-7",
            1,
            config=config,
            walk=walk,
            aggregators=aggregators,
            tracker=tracker,
            db=db,
            redis=None,
            session_id=config.session_id,
            seq=1,
            available_usdc=10000.0,
            open_positions=open_positions,
            nav_table="NAV",
            positions_table="No open positions.",
            recent_decisions="None",
            elapsed_seconds=0.0,
        )

    # Result must be rejected
    assert result["status"] == "rejected", f"Expected rejected, got: {result}"
    assert "no open position" in result.get("reason", "").lower(), (
        f"Expected 'no open position' in reason, got: {result.get('reason')}"
    )
    # No MockPerps call should have been made
    assert not close_called, "closePosition was called despite no open position"
    # Journal should have been called (the intent row + reconcile)
    assert len(journal_calls) >= 1, "Expected journal entry for rejected close, got none"


# ---------------------------------------------------------------------------
# (c) D-10 limit rejects a 2nd open on a market with an existing on-chain position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d10_rejects_second_open_for_same_market() -> None:
    """D-10: a 2nd open for a market already in open_positions must be rejected.

    WR-03 regression test — the D-10 check was previously vacuous because
    open_positions was never populated.  With _build_open_positions providing
    real on-chain data, this test verifies the gate fires.
    """
    from orchestrator.business_rules import validate_business_rules

    # Simulate ETH already open (as if populated from chain)
    open_positions = {
        "ETH": {
            "position_key": "0x" + "aa" * 32,
            "side": "long",
            "size_usd": 1000.0,
        }
    }

    # Another open for ETH — should hit D-10
    open_decision = _make_decision(action="open", market="ETH", side="long", sizeUsd=500.0)

    # Verify business_rules rejects it with the populated open_positions
    reason = validate_business_rules(
        open_decision, available_usdc=10000.0, open_positions=open_positions
    )
    assert reason is not None, (
        "D-10 business rule did not reject a 2nd open for ETH when open_positions was populated. "
        "This is the WR-03 regression — open_positions must come from on-chain state."
    )
    assert "ETH" in reason or "position" in reason.lower(), (
        f"Expected market/position mention in rejection reason, got: {reason}"
    )


# ---------------------------------------------------------------------------
# _build_open_positions helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_open_positions_returns_market_map() -> None:
    """_build_open_positions correctly maps open positions from chain state."""
    mock_perps = MagicMock()

    eth_key = bytes.fromhex("aa" * 32)
    btc_key = bytes.fromhex("bb" * 32)

    # getOpenPositionKeys returns two keys
    mock_perps.functions.getOpenPositionKeys.return_value.call = AsyncMock(
        return_value=[eth_key, btc_key]
    )

    # positions struct: (market, signedSize, entryPrice, collateral, vault, closed)
    # ETH long 1000 USD (1e30-scaled)
    eth_size_1e30 = int(1000 * 1e30)
    # BTC short 2000 USD (negative)
    btc_size_1e30 = -int(2000 * 1e30)

    def fake_position(key_bytes):
        mock = MagicMock()
        if key_bytes == eth_key:
            mock.call = AsyncMock(
                return_value=("ETH", eth_size_1e30, int(3000e8), int(500e6), "0xVault", False)
            )
        else:
            mock.call = AsyncMock(
                return_value=("BTC", btc_size_1e30, int(60000e8), int(1000e6), "0xVault", False)
            )
        return mock

    mock_perps.functions.positions = MagicMock(side_effect=fake_position)

    result = await _build_open_positions(mock_perps, "0xVault")

    assert "ETH" in result, "ETH should be in open_positions"
    assert result["ETH"]["side"] == "long"
    assert abs(result["ETH"]["size_usd"] - 1000.0) < 0.01

    assert "BTC" in result, "BTC should be in open_positions"
    assert result["BTC"]["side"] == "short"
    assert abs(result["BTC"]["size_usd"] - 2000.0) < 0.01


@pytest.mark.asyncio
async def test_build_open_positions_returns_empty_on_error() -> None:
    """_build_open_positions returns {} when getOpenPositionKeys raises."""
    mock_perps = MagicMock()
    mock_perps.functions.getOpenPositionKeys.return_value.call = AsyncMock(
        side_effect=Exception("RPC error")
    )

    result = await _build_open_positions(mock_perps, "0xVault")
    assert result == {}, "Expected empty dict on RPC error, got non-empty"

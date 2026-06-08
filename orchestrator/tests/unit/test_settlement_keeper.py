"""Unit tests for orchestrator.loop.settlement_keeper (GAP #9).

Tests:
  (i)   drain loop closes every open position key; executeOrder retried on "too early".
  (ii)  endSession is skipped-with-clear-log when not yet permitted (pre-deadline, non-factory).
  (iii) failure paths alert via the sink, never crash (vault.closePosition revert).
  (iv)  failure paths alert via the sink, never crash (executeOrder timeout).
  (v)   already_settled short-circuits cleanly (idempotent).
  (vi)  no open positions → positionValueUSDC already 0 → endSession called directly.
  (vii) "not authorized before deadline" revert string → not_permitted status (string-match path).
  (viii) full happy path: open positions → close → wait → executeOrder → value==0 → endSession.

All external dependencies are faked via AsyncMock/MagicMock — no anvil, no Postgres required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_web3(*, block_number: int = 100, block_ts: int = 9_999_999) -> MagicMock:
    """Build a minimal AsyncWeb3-like mock."""
    web3 = MagicMock()
    web3.eth.get_block_number = AsyncMock(return_value=block_number)
    web3.eth.get_block = AsyncMock(return_value={"timestamp": block_ts, "number": block_number})
    web3.eth.wait_for_transaction_receipt = AsyncMock(
        return_value={"blockNumber": block_number, "status": 1}
    )
    return web3


def _make_mock_perps(
    *,
    open_keys: list[bytes] | None = None,
    pos_value: int = 0,
    execute_order_raises: Exception | None = None,
    execute_order_raises_once: Exception | None = None,
) -> MagicMock:
    """Build a minimal MockPerps-like mock."""
    mp = MagicMock()
    _open_keys = open_keys or []

    mp.functions.getOpenPositionKeys.return_value.call = AsyncMock(return_value=_open_keys)
    mp.functions.positionValueUSDC.return_value.call = AsyncMock(return_value=pos_value)

    if execute_order_raises is not None:
        mp.functions.executeOrder.return_value.transact = AsyncMock(
            side_effect=execute_order_raises
        )
    elif execute_order_raises_once is not None:
        # Fail once (too early), succeed on second attempt
        call_count = {"n": 0}
        fake_tx = b"\xde\xad" + b"\x00" * 30

        async def _transact(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise execute_order_raises_once
            return fake_tx

        mp.functions.executeOrder.return_value.transact = _transact
    else:
        mp.functions.executeOrder.return_value.transact = AsyncMock(
            return_value=b"\xde\xad" + b"\x00" * 30
        )

    # OrderExecuted event parsing — returns one event by default
    fake_executed_event = {"args": {"orderKey": b"\x00" * 32, "positionKey": b"\x00" * 32}}
    mp.events.OrderExecuted.return_value.process_receipt = MagicMock(
        return_value=[fake_executed_event]
    )
    mp.events.PositionLiquidated.return_value.process_receipt = MagicMock(return_value=[])

    # OrderCreated event (returned by closePosition receipt parsing)
    order_key_bytes = b"\xca\xfe" + b"\x00" * 30
    fake_order_created_event = {"args": {"orderKey": order_key_bytes, "positionKey": b"\x00" * 32}}
    mp.events.OrderCreated.return_value.process_receipt = MagicMock(
        return_value=[fake_order_created_event]
    )

    return mp


def _make_vault_contract(*, close_reverts: bool = False) -> MagicMock:
    """Build a minimal MTokenVault-like mock."""
    vc = MagicMock()
    if close_reverts:
        vc.functions.closePosition.return_value.transact = AsyncMock(
            side_effect=Exception("execution reverted: Vault: order in flight")
        )
    else:
        vc.functions.closePosition.return_value.transact = AsyncMock(
            return_value=b"\xbe\xef" + b"\x00" * 30
        )
    return vc


def _make_settlement_contract(
    *,
    already_settled: bool = False,
    deadline: int = 1_000_000,  # far future by default
    end_session_reverts: Exception | None = None,
) -> MagicMock:
    """Build a minimal SettlementContract-like mock."""
    sc = MagicMock()
    sc.functions.settled.return_value.call = AsyncMock(return_value=already_settled)
    sc.functions.deadline.return_value.call = AsyncMock(return_value=deadline)

    if end_session_reverts is not None:
        sc.functions.endSession.return_value.transact = AsyncMock(side_effect=end_session_reverts)
    else:
        sc.functions.endSession.return_value.transact = AsyncMock(
            return_value=b"\xaa\xbb" + b"\x00" * 30
        )
    return sc


# ---------------------------------------------------------------------------
# (i) drain loop closes every open key; executeOrder retried on "too early"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_loop_closes_all_open_keys_with_retry() -> None:
    """drain_and_settle closes each open position; executeOrder is retried on 'too early'."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    pos_key1 = b"\x01" * 32
    pos_key2 = b"\x02" * 32

    web3 = _make_web3(block_ts=9_999_999)  # past deadline
    mp = _make_mock_perps(
        open_keys=[pos_key1, pos_key2],
        pos_value=0,
        execute_order_raises_once=Exception("MockPerps: too early"),
    )
    vc = _make_vault_contract()
    sc = _make_settlement_contract(deadline=1)  # deadline in the past → permitted

    with patch("orchestrator.loop.settlement_keeper.send_alert"):
        result = await drain_and_settle(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            poll_interval_seconds=0.001,
            value_check_interval_seconds=0.001,
        )

    assert result["status"] == "settled", f"Expected 'settled', got {result}"
    # Two close orders submitted (one per open position)
    assert result["positions_closed"] == 2, (
        f"Expected 2 positions closed, got {result['positions_closed']}"
    )
    # vault.closePosition called twice (once per open position key)
    assert vc.functions.closePosition.call_count == 2


# ---------------------------------------------------------------------------
# (ii) endSession skipped with clear log when pre-deadline, non-factory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_session_skipped_when_pre_deadline() -> None:
    """If session deadline has not passed, endSession is not called; status='not_permitted'."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    block_ts = 1_000  # now
    deadline = 2_000  # deadline in the future

    web3 = _make_web3(block_ts=block_ts)
    mp = _make_mock_perps(open_keys=[], pos_value=0)
    vc = _make_vault_contract()
    sc = _make_settlement_contract(deadline=deadline)

    with patch("orchestrator.loop.settlement_keeper.send_alert"):
        result = await drain_and_settle(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            poll_interval_seconds=0.001,
        )

    assert result["status"] == "not_permitted", f"Expected 'not_permitted', got {result}"
    # endSession must NOT have been called
    sc.functions.endSession.assert_not_called()


# ---------------------------------------------------------------------------
# (iii) closePosition revert → CRITICAL alert, keeper does NOT crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_position_revert_alerts_and_continues() -> None:
    """vault.closePosition revert fires CRITICAL alert; keeper does not crash."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    pos_key = b"\x03" * 32

    web3 = _make_web3(block_ts=9_999_999)
    mp = _make_mock_perps(open_keys=[pos_key], pos_value=0)
    vc = _make_vault_contract(close_reverts=True)
    sc = _make_settlement_contract(deadline=1)

    alert_calls: list[dict] = []

    async def fake_send_alert(message, severity, *, context=None, **kw):
        alert_calls.append({"message": message, "severity": severity})

    with patch("orchestrator.loop.settlement_keeper.send_alert", side_effect=fake_send_alert):
        result = await drain_and_settle(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            poll_interval_seconds=0.001,
            value_check_interval_seconds=0.001,
        )

    # Did not crash — result is a dict
    assert isinstance(result, dict)
    # A CRITICAL alert was fired for the closePosition failure
    from orchestrator.alerts.sink import AlertSeverity

    critical = [a for a in alert_calls if a["severity"] == AlertSeverity.CRITICAL]
    assert len(critical) >= 1, (
        f"Expected CRITICAL alert after closePosition revert; got {alert_calls}"
    )


# ---------------------------------------------------------------------------
# (iv) executeOrder timeout → drain_timeout status + CRITICAL alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_order_timeout_fires_critical_alert() -> None:
    """If executeOrder never succeeds within max_drain_wait_blocks, status='drain_timeout'."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    pos_key = b"\x04" * 32

    # Execute always raises "too early" → will exceed block budget
    web3 = _make_web3(block_number=100, block_ts=9_999_999)

    # Block number increments each poll to simulate time passing and exceed budget
    call_counter = {"n": 0}

    async def _get_block_number():
        call_counter["n"] += 1
        # Start at 100, increment by 2 each call to quickly exceed the tiny budget
        return 100 + call_counter["n"] * 2

    web3.eth.get_block_number = _get_block_number

    mp = _make_mock_perps(
        open_keys=[pos_key],
        pos_value=0,
        execute_order_raises=Exception("MockPerps: too early"),
    )
    vc = _make_vault_contract()
    sc = _make_settlement_contract(deadline=1)

    alert_calls: list[dict] = []

    async def fake_send_alert(message, severity, *, context=None, **kw):
        alert_calls.append({"message": message, "severity": severity})

    with patch("orchestrator.loop.settlement_keeper.send_alert", side_effect=fake_send_alert):
        result = await drain_and_settle(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            max_drain_wait_blocks=3,  # tiny budget so we timeout quickly
            poll_interval_seconds=0.001,
        )

    assert result["status"] == "drain_timeout", f"Expected 'drain_timeout', got {result}"
    from orchestrator.alerts.sink import AlertSeverity

    critical = [a for a in alert_calls if a["severity"] == AlertSeverity.CRITICAL]
    assert len(critical) >= 1, "Expected CRITICAL alert after executeOrder timeout"


# ---------------------------------------------------------------------------
# (v) already_settled → short-circuits cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_settled_short_circuits() -> None:
    """If settlement.settled == True, drain_and_settle returns 'already_settled' immediately."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    web3 = _make_web3()
    mp = _make_mock_perps()
    vc = _make_vault_contract()
    sc = _make_settlement_contract(already_settled=True)

    result = await drain_and_settle(
        web3,
        mp,
        sc,
        vc,
        vault_address="0x" + "A" * 40,
        orchestrator_address="0x" + "B" * 40,
        deployer_address="0x" + "C" * 40,
    )

    assert result["status"] == "already_settled"
    # Nothing should have been called (no close, no execute, no endSession)
    mp.functions.getOpenPositionKeys.assert_not_called()
    sc.functions.endSession.assert_not_called()


# ---------------------------------------------------------------------------
# (vi) no open positions → positionValueUSDC == 0 → endSession called directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_open_positions_calls_end_session_directly() -> None:
    """If no positions are open, skip drain loop and call endSession immediately."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    web3 = _make_web3(block_ts=9_999_999)
    mp = _make_mock_perps(open_keys=[], pos_value=0)
    vc = _make_vault_contract()
    sc = _make_settlement_contract(deadline=1)  # deadline in past → permitted

    with patch("orchestrator.loop.settlement_keeper.send_alert"):
        result = await drain_and_settle(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            poll_interval_seconds=0.001,
            value_check_interval_seconds=0.001,
        )

    assert result["status"] == "settled", f"Expected 'settled', got {result}"
    # No closePosition calls (no open positions)
    vc.functions.closePosition.assert_not_called()
    # endSession was called exactly once
    sc.functions.endSession.return_value.transact.assert_called_once()


# ---------------------------------------------------------------------------
# (vii) "not authorized before deadline" revert → not_permitted (string-match path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_session_not_authorized_revert_returns_not_permitted() -> None:
    """endSession revert 'not authorized' → status='not_permitted', no crash, no CRITICAL alert."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    web3 = _make_web3(block_ts=9_999_999)
    mp = _make_mock_perps(open_keys=[], pos_value=0)
    vc = _make_vault_contract()
    # Deadline is in the past BUT endSession raises "not authorized" (simulates pre-deadline check
    # failing e.g. when block_ts read is stale or deadline() read fails)
    sc = _make_settlement_contract(
        deadline=1,
        end_session_reverts=Exception(
            "execution reverted: Settlement: not authorized before deadline"
        ),
    )

    alert_calls: list[dict] = []

    async def fake_send_alert(message, severity, *, context=None, **kw):
        alert_calls.append({"message": message, "severity": severity})

    with patch("orchestrator.loop.settlement_keeper.send_alert", side_effect=fake_send_alert):
        result = await drain_and_settle(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            poll_interval_seconds=0.001,
            value_check_interval_seconds=0.001,
        )

    assert result["status"] == "not_permitted", f"Expected 'not_permitted', got {result}"
    # No CRITICAL alert for a not_permitted result
    from orchestrator.alerts.sink import AlertSeverity

    critical = [a for a in alert_calls if a["severity"] == AlertSeverity.CRITICAL]
    assert len(critical) == 0, f"No CRITICAL alert expected for 'not_permitted'; got {alert_calls}"


# ---------------------------------------------------------------------------
# (viii) full happy path: open position → close → executeOrder → value==0 → endSession
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_happy_path_open_position_to_settled() -> None:
    """Full settlement flow: one open position → close order → executeOrder → endSession."""
    from orchestrator.loop.settlement_keeper import drain_and_settle

    pos_key = b"\x05" * 32
    web3 = _make_web3(block_ts=9_999_999)

    # positionValueUSDC returns 0 on first call (after close orders executed)
    mp = _make_mock_perps(open_keys=[pos_key], pos_value=0)
    vc = _make_vault_contract()
    sc = _make_settlement_contract(deadline=1)

    with patch("orchestrator.loop.settlement_keeper.send_alert"):
        result = await drain_and_settle(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            poll_interval_seconds=0.001,
            value_check_interval_seconds=0.001,
        )

    assert result["status"] == "settled", f"Expected 'settled', got {result}"
    assert result["positions_closed"] == 1

    # vault.closePosition called exactly once (one open key)
    vc.functions.closePosition.assert_called_once()
    # executeOrder called (async close execution)
    mp.functions.executeOrder.return_value.transact.assert_called()
    # endSession called once
    sc.functions.endSession.return_value.transact.assert_called_once()


# ---------------------------------------------------------------------------
# (ix) run_settlement_keeper — thin wrapper just delegates to drain_and_settle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_settlement_keeper_delegates_to_drain_and_settle() -> None:
    """run_settlement_keeper is a thin wrapper; it returns drain_and_settle's result."""
    from orchestrator.loop.settlement_keeper import run_settlement_keeper

    web3 = _make_web3(block_ts=9_999_999)
    mp = _make_mock_perps(open_keys=[], pos_value=0)
    vc = _make_vault_contract()
    sc = _make_settlement_contract(deadline=1)

    with patch("orchestrator.loop.settlement_keeper.send_alert"):
        result = await run_settlement_keeper(
            web3,
            mp,
            sc,
            vc,
            vault_address="0x" + "A" * 40,
            orchestrator_address="0x" + "B" * 40,
            deployer_address="0x" + "C" * 40,
            poll_interval_seconds=0.001,
            value_check_interval_seconds=0.001,
        )

    assert result["status"] == "settled"

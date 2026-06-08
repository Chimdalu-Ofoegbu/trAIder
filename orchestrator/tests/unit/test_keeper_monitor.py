"""Unit tests for orchestrator.loop.keeper_monitor (D-13 / ORCH-08).

Tests:
  (i)   A ready order triggers executeOrder + mark_pending_order_executed.
  (ii)  A too-early / raised executeOrder leaves the order unmarked (retry next poll).
  (iii) run_keeper_monitor exits when stop_event is set.
  (iv)  [VAULT-06 regression] On OrderExecuted, clearTradingLock is called with the
        correct order_key bytes, from the orchestrator address.
  (v)   [VAULT-06 regression] clearTradingLock failure is logged at ERROR and fires the
        alert sink; it does NOT silently swallow.
  (vi)  [VAULT-06 regression] clearTradingLock is skipped (debug-only) when vault_contract
        or orchestrator_address are not provided — backward-compat with existing callers.

All external dependencies (web3, mock_perps, db helpers, asyncio) are faked via
AsyncMock / MagicMock — no anvil or Postgres required.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_ready_order_triggers_execute_and_mark() -> None:
    """A ready order triggers executeOrder and mark_pending_order_executed (i).

    Flow:
    - get_pending_orders_ready returns one order dict.
    - executeOrder returns a fake tx hash.
    - get_transaction_receipt returns a receipt.
    - OrderExecuted event is found in the receipt.
    - record_trade is called with the order's decision_snapshot data.
    - mark_pending_order_executed is called with the order's key.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestrator.loop.keeper_monitor import execute_ready_orders

    order_key_hex = "0xabcd1234" + "0" * 56
    order_key_bytes = bytes.fromhex(order_key_hex.removeprefix("0x"))

    fake_order = {
        "id": "some-uuid",
        "vault_address": "0xVault",
        "order_key": order_key_hex,
        "session_id": "00000000-0000-0000-0000-000000000001",
        "execute_after_block": 5,
        "status": "pending",
        "decision_snapshot": {
            "action": "open",
            "market": "ETH",
            "side": "long",
            "sizeUsd": 1000.0,
            "leverage": 2.0,
            "reasoning": "test",
        },
    }

    # web3 mock
    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=10)
    fake_exec_tx = b"\xde\xad\xbe\xef" + b"\x00" * 28
    web3.eth.get_transaction_receipt = AsyncMock(
        return_value={"blockNumber": 10, "blockHash": b"\x00" * 32}
    )

    # mock_perps mock
    mock_perps = MagicMock()
    mock_perps.functions.executeOrder.return_value.transact = AsyncMock(return_value=fake_exec_tx)

    # OrderExecuted event — returns a list with one parsed event
    fake_event_data = {
        "args": {
            "orderKey": order_key_bytes,
            "positionKey": b"\x00" * 32,
        }
    }
    mock_perps.events.OrderExecuted.return_value.process_receipt = MagicMock(
        return_value=[fake_event_data]
    )

    db_session = AsyncMock()

    marks_executed: list[str] = []
    trades_recorded: list[dict] = []

    async def fake_get_ready(sess, block, *, vault_address=None):
        return [fake_order]

    async def fake_mark_executed(sess, *, vault_address, order_key):
        marks_executed.append(order_key)

    async def fake_record_trade(
        sess,
        *,
        vault_address,
        session_id,
        order_key,
        market,
        side,
        action,
        size_usdc,
        onchain_tx,
        block_number,
        **kw,
    ):
        trades_recorded.append({"order_key": order_key, "market": market, "side": side})
        return "0xtradehash"

    with (
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready", side_effect=fake_get_ready
        ),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            side_effect=fake_mark_executed,
        ),
        patch("orchestrator.loop.keeper_monitor.record_trade", side_effect=fake_record_trade),
    ):
        results = await execute_ready_orders(
            web3,
            mock_perps,
            db_session,
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=None,
            session_id="00000000-0000-0000-0000-000000000001",
            seq_counter=1,
        )

    assert len(results) == 1
    assert results[0]["status"] == "executed"
    assert results[0]["order_key"] == order_key_hex
    assert order_key_hex in marks_executed
    assert len(trades_recorded) == 1
    assert trades_recorded[0]["market"] == "ETH"


@pytest.mark.asyncio
async def test_too_early_execute_order_leaves_order_unmarked() -> None:
    """A raised executeOrder (e.g. 'too early') leaves the order unmarked — retry (ii)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestrator.loop.keeper_monitor import execute_ready_orders

    order_key_hex = "0xdeadbeef" + "0" * 56
    fake_order = {
        "id": "some-uuid",
        "vault_address": "0xVault",
        "order_key": order_key_hex,
        "session_id": "00000000-0000-0000-0000-000000000001",
        "execute_after_block": 5,
        "status": "pending",
        "decision_snapshot": {
            "action": "open",
            "market": "BTC",
            "side": "short",
            "sizeUsd": 2000.0,
            "leverage": 1.0,
            "reasoning": "test",
        },
    }

    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=10)

    mock_perps = MagicMock()
    mock_perps.functions.executeOrder.return_value.transact = AsyncMock(
        side_effect=Exception("execution reverted: too early")
    )

    db_session = AsyncMock()
    marks_executed: list[str] = []

    async def fake_get_ready(sess, block, *, vault_address=None):
        return [fake_order]

    async def fake_mark_executed(sess, *, vault_address, order_key):
        marks_executed.append(order_key)

    with (
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready", side_effect=fake_get_ready
        ),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            side_effect=fake_mark_executed,
        ),
        patch("orchestrator.loop.keeper_monitor.record_trade"),
    ):
        results = await execute_ready_orders(
            web3,
            mock_perps,
            db_session,
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=None,
            session_id="00000000-0000-0000-0000-000000000001",
            seq_counter=1,
        )

    # The order should NOT be marked executed — left for retry
    assert marks_executed == [], f"Expected no marks_executed but got {marks_executed}"
    # Result reflects the failure
    assert len(results) == 1
    assert results[0]["status"] == "error"


@pytest.mark.asyncio
async def test_run_keeper_monitor_exits_on_stop_event() -> None:
    """run_keeper_monitor exits cleanly when stop_event is set (iii)."""
    from unittest.mock import AsyncMock, patch

    from orchestrator.loop.keeper_monitor import run_keeper_monitor

    web3 = AsyncMock()
    mock_perps = AsyncMock()
    db_session = AsyncMock()
    stop_event = asyncio.Event()
    redis = None

    call_count = 0

    async def fake_execute_ready_orders(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Set stop_event after the first poll so the loop exits
        stop_event.set()
        return []

    with patch(
        "orchestrator.loop.keeper_monitor.execute_ready_orders",
        side_effect=fake_execute_ready_orders,
    ):
        await run_keeper_monitor(
            web3,
            mock_perps,
            db_session,
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=redis,
            session_id="00000000-0000-0000-0000-000000000001",
            stop_event=stop_event,
            poll_seconds=0.01,
        )

    # Should have called exactly once before the stop_event caused exit
    assert call_count >= 1, "Keeper should have polled at least once"
    assert stop_event.is_set()


# ---------------------------------------------------------------------------
# VAULT-06 regression tests: clearTradingLock after OrderExecuted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_trading_lock_called_after_order_executed() -> None:
    """VAULT-06 regression (iv): clearTradingLock is called with the correct order_key
    bytes and from the orchestrator address after OrderExecuted.

    Without this fix, _tradingLocked stays true after the first trade and every subsequent
    openLong/openShort/closePosition reverts "Vault: order in flight".
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestrator.loop.keeper_monitor import execute_ready_orders

    order_key_hex = "0xabcd1234" + "0" * 56
    order_key_bytes = bytes.fromhex(order_key_hex.removeprefix("0x"))
    orchestrator_addr = "0x65A4e4DDc9Fe83A2c715959c8EaE6b0645824c4A"

    fake_order = {
        "id": "some-uuid",
        "vault_address": "0xVault",
        "order_key": order_key_hex,
        "session_id": "00000000-0000-0000-0000-000000000001",
        "execute_after_block": 5,
        "status": "pending",
        "decision_snapshot": {
            "action": "open",
            "market": "ETH",
            "side": "long",
            "sizeUsd": 1000.0,
            "leverage": 2.0,
            "reasoning": "test",
        },
    }

    # web3 mock — wait_for_transaction_receipt used for both executeOrder and clearTradingLock
    fake_exec_tx = b"\xde\xad\xbe\xef" + b"\x00" * 28
    fake_clear_tx = b"\xca\xfe\xba\xbe" + b"\x00" * 28
    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=10)
    # Return success receipts for both transactions
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"blockNumber": 10, "status": 1})

    # mock_perps mock
    mock_perps = MagicMock()
    mock_perps.functions.executeOrder.return_value.transact = AsyncMock(return_value=fake_exec_tx)
    fake_event_data = {"args": {"orderKey": order_key_bytes, "positionKey": b"\x00" * 32}}
    mock_perps.events.OrderExecuted.return_value.process_receipt = MagicMock(
        return_value=[fake_event_data]
    )

    # vault_contract mock — tracks what clearTradingLock was called with
    clear_lock_calls: list[dict] = []
    vault_contract = MagicMock()

    def _clear_lock_fn(ok_bytes):
        # Store what order_key bytes were passed
        recorded_bytes = ok_bytes

        obj = MagicMock()

        async def recording_transact(tx_params):
            clear_lock_calls.append(
                {"order_key_bytes": recorded_bytes, "from": tx_params.get("from")}
            )
            return fake_clear_tx

        obj.transact = recording_transact
        return obj

    vault_contract.functions.clearTradingLock = MagicMock(side_effect=_clear_lock_fn)

    db_session = AsyncMock()

    async def fake_get_ready(sess, block, *, vault_address=None):
        return [fake_order]

    async def fake_mark_executed(sess, *, vault_address, order_key):
        pass

    async def fake_record_trade(
        sess,
        *,
        vault_address,
        session_id,
        order_key,
        market,
        side,
        action,
        size_usdc,
        onchain_tx,
        block_number,
        **kw,
    ):
        return "0xtradehash"

    with (
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready", side_effect=fake_get_ready
        ),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            side_effect=fake_mark_executed,
        ),
        patch("orchestrator.loop.keeper_monitor.record_trade", side_effect=fake_record_trade),
        patch("orchestrator.loop.keeper_monitor.publish_journal_entry"),
        patch("orchestrator.loop.keeper_monitor.send_alert"),
    ):
        results = await execute_ready_orders(
            web3,
            mock_perps,
            db_session,
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=None,
            session_id="00000000-0000-0000-0000-000000000001",
            seq_counter=1,
            vault_contract=vault_contract,
            orchestrator_address=orchestrator_addr,
        )

    assert len(results) == 1
    assert results[0]["status"] == "executed"

    # VAULT-06 assertion: clearTradingLock must have been called exactly once
    assert len(clear_lock_calls) == 1, (
        f"Expected clearTradingLock to be called once after OrderExecuted, "
        f"got {len(clear_lock_calls)} calls. "
        "VAULT-06 regression: vault stays locked after first trade without this call."
    )
    # With the correct order_key bytes
    assert clear_lock_calls[0]["order_key_bytes"] == order_key_bytes, (
        f"clearTradingLock called with wrong order_key. "
        f"Expected {order_key_bytes.hex()!r}, got {clear_lock_calls[0]['order_key_bytes'].hex()!r}"
    )
    # From the orchestrator address (onlyOrchestrator modifier)
    assert clear_lock_calls[0]["from"] == orchestrator_addr, (
        f"clearTradingLock must be called from the orchestrator EOA. "
        f"Expected {orchestrator_addr!r}, got {clear_lock_calls[0]['from']!r}"
    )

    # Also assert vault_contract.functions.clearTradingLock was called (belt-and-suspenders)
    vault_contract.functions.clearTradingLock.assert_called_once_with(order_key_bytes)


@pytest.mark.asyncio
async def test_clear_trading_lock_failure_logs_error_and_alerts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """VAULT-06 regression (v): if clearTradingLock fails, it is logged at ERROR and the
    alert sink is fired with CRITICAL severity. It must NOT be silently swallowed.

    A stuck trading lock bricks the entire session — loud failure is mandatory.
    """
    import logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestrator.loop.keeper_monitor import execute_ready_orders

    order_key_hex = "0xdeadbeef" + "0" * 56
    order_key_bytes = bytes.fromhex(order_key_hex.removeprefix("0x"))
    orchestrator_addr = "0x65A4e4DDc9Fe83A2c715959c8EaE6b0645824c4A"

    fake_order = {
        "id": "some-uuid",
        "vault_address": "0xVault",
        "order_key": order_key_hex,
        "session_id": "00000000-0000-0000-0000-000000000002",
        "execute_after_block": 5,
        "status": "pending",
        "decision_snapshot": {
            "action": "open",
            "market": "BTC",
            "side": "short",
            "sizeUsd": 2000.0,
            "leverage": 1.0,
            "reasoning": "test",
        },
    }

    fake_exec_tx = b"\xde\xad\xbe\xef" + b"\x00" * 28
    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=10)
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"blockNumber": 10, "status": 1})

    mock_perps = MagicMock()
    mock_perps.functions.executeOrder.return_value.transact = AsyncMock(return_value=fake_exec_tx)
    fake_event_data = {"args": {"orderKey": order_key_bytes, "positionKey": b"\x00" * 32}}
    mock_perps.events.OrderExecuted.return_value.process_receipt = MagicMock(
        return_value=[fake_event_data]
    )

    # vault_contract.functions.clearTradingLock(...).transact(...) raises — simulates revert
    vault_contract = MagicMock()
    clear_fn_obj = MagicMock()
    clear_fn_obj.transact = AsyncMock(side_effect=Exception("execution reverted: onlyOrchestrator"))
    vault_contract.functions.clearTradingLock = MagicMock(return_value=clear_fn_obj)

    db_session = AsyncMock()

    async def fake_get_ready(sess, block, *, vault_address=None):
        return [fake_order]

    async def fake_mark_executed(sess, *, vault_address, order_key):
        pass

    async def fake_record_trade(
        sess,
        *,
        vault_address,
        session_id,
        order_key,
        market,
        side,
        action,
        size_usdc,
        onchain_tx,
        block_number,
        **kw,
    ):
        return "0xtradehash"

    alert_calls: list[dict] = []

    async def fake_send_alert(message, severity, *, context=None, **kw):
        alert_calls.append({"message": message, "severity": severity, "context": context})

    with (
        caplog.at_level(logging.ERROR, logger="orchestrator.loop.keeper_monitor"),
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready", side_effect=fake_get_ready
        ),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            side_effect=fake_mark_executed,
        ),
        patch("orchestrator.loop.keeper_monitor.record_trade", side_effect=fake_record_trade),
        patch("orchestrator.loop.keeper_monitor.publish_journal_entry"),
        patch("orchestrator.loop.keeper_monitor.send_alert", side_effect=fake_send_alert),
    ):
        results = await execute_ready_orders(
            web3,
            mock_perps,
            db_session,
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=None,
            session_id="00000000-0000-0000-0000-000000000002",
            seq_counter=1,
            vault_contract=vault_contract,
            orchestrator_address=orchestrator_addr,
        )

    # Order is still considered executed (record_trade + mark happened)
    assert len(results) == 1
    assert results[0]["status"] == "executed"

    # ERROR-level log must be emitted (not silently swallowed)
    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "clearTradingLock" in msg.lower() or "critical" in msg.lower() or "locked" in msg.lower()
        for msg in error_msgs
    ), (
        f"Expected an ERROR log about clearTradingLock failure; got: {error_msgs}. "
        "VAULT-06: stuck lock must be logged loudly at ERROR."
    )

    # Alert sink must be fired (CRITICAL severity)
    from orchestrator.alerts.sink import AlertSeverity

    assert len(alert_calls) >= 1, (
        "Expected send_alert to be called after clearTradingLock failure. "
        "VAULT-06: stuck lock must fire alert sink."
    )
    critical_alerts = [a for a in alert_calls if a["severity"] == AlertSeverity.CRITICAL]
    assert len(critical_alerts) >= 1, (
        f"Expected at least one CRITICAL alert after clearTradingLock failure; "
        f"got severities: {[a['severity'] for a in alert_calls]}. "
        "VAULT-06: stuck lock is a CRITICAL severity event."
    )


@pytest.mark.asyncio
async def test_clear_trading_lock_skipped_when_not_wired() -> None:
    """VAULT-06 regression (vi): clearTradingLock is NOT called when vault_contract or
    orchestrator_address are not provided — backward-compat with Phase-2 anvil tests.

    The keeper should still mark the order executed and NOT raise or log an ERROR.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestrator.loop.keeper_monitor import execute_ready_orders

    order_key_hex = "0xcafe1234" + "0" * 56
    order_key_bytes = bytes.fromhex(order_key_hex.removeprefix("0x"))

    fake_order = {
        "id": "some-uuid",
        "vault_address": "0xVault",
        "order_key": order_key_hex,
        "session_id": "00000000-0000-0000-0000-000000000003",
        "execute_after_block": 5,
        "status": "pending",
        "decision_snapshot": {
            "action": "open",
            "market": "SOL",
            "side": "long",
            "sizeUsd": 500.0,
            "leverage": 3.0,
            "reasoning": "test",
        },
    }

    fake_exec_tx = b"\xca\xfe\x12\x34" + b"\x00" * 28
    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=10)
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"blockNumber": 10, "status": 1})

    mock_perps = MagicMock()
    mock_perps.functions.executeOrder.return_value.transact = AsyncMock(return_value=fake_exec_tx)
    fake_event_data = {"args": {"orderKey": order_key_bytes, "positionKey": b"\x00" * 32}}
    mock_perps.events.OrderExecuted.return_value.process_receipt = MagicMock(
        return_value=[fake_event_data]
    )

    # No vault_contract provided (legacy path)
    db_session = AsyncMock()

    async def fake_get_ready(sess, block, *, vault_address=None):
        return [fake_order]

    async def fake_mark_executed(sess, *, vault_address, order_key):
        pass

    async def fake_record_trade(
        sess,
        *,
        vault_address,
        session_id,
        order_key,
        market,
        side,
        action,
        size_usdc,
        onchain_tx,
        block_number,
        **kw,
    ):
        return "0xtradehash"

    with (
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready", side_effect=fake_get_ready
        ),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            side_effect=fake_mark_executed,
        ),
        patch("orchestrator.loop.keeper_monitor.record_trade", side_effect=fake_record_trade),
        patch("orchestrator.loop.keeper_monitor.publish_journal_entry"),
        patch("orchestrator.loop.keeper_monitor.send_alert") as mock_send_alert,
    ):
        # vault_contract=None (not provided) — backward-compat path
        results = await execute_ready_orders(
            web3,
            mock_perps,
            db_session,
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=None,
            session_id="00000000-0000-0000-0000-000000000003",
            seq_counter=1,
            # vault_contract and orchestrator_address intentionally omitted
        )

    assert len(results) == 1
    assert results[0]["status"] == "executed"

    # No CRITICAL alert should be fired — clearTradingLock was skipped cleanly
    from orchestrator.alerts.sink import AlertSeverity

    critical_alerts = [
        call
        for call in mock_send_alert.call_args_list
        if len(call.args) >= 2 and call.args[1] == AlertSeverity.CRITICAL
    ]
    assert len(critical_alerts) == 0, (
        "send_alert with CRITICAL severity must NOT be called when vault_contract is not provided. "
        "VAULT-06: skipping clearTradingLock on legacy path must be silent (debug only)."
    )

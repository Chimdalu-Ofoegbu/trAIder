"""Unit tests for orchestrator.loop.keeper_monitor (D-13 / ORCH-08).

Tests:
  (i)  A ready order triggers executeOrder + mark_pending_order_executed.
  (ii) A too-early / raised executeOrder leaves the order unmarked (retry next poll).
  (iii) run_keeper_monitor exits when stop_event is set.

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

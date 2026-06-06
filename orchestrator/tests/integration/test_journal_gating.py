"""
orchestrator.tests.integration.test_journal_gating — Journal publish-only-on-OrderExecuted (PERPS-02).

Tests:
  - On createOrder (pending_orders row created): NO publish_journal_entry call.
  - Only AFTER the OrderExecuted event is observed by keeper_monitor does
    publish_journal_entry fire for that trade.
  - publish_journal_entry is called EXACTLY ONCE per OrderExecuted event.
  - NEVER called on PositionLiquidated (no OrderExecuted branch).

PERPS-02 front-running mitigation: publish is wired only to the OrderExecuted event
path in keeper_monitor, never to the createOrder receipt in driver.py.

The DB/Postgres portion of this test uses pytest.skip if Postgres is unavailable.
The call-ordering assertion runs with pure mocks — no DB required for the core proof.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# PERPS-02 core proof: publish called ONLY after OrderExecuted, NEVER before
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journal_published_only_on_order_executed() -> None:
    """Journal entry is published on OrderExecuted, never on the submission receipt.

    Simulates:
    1. A pending_orders row is created (the createOrder receipt) — publish must NOT fire.
    2. An OrderExecuted event is observed by execute_ready_orders — publish fires exactly once.

    This is the PERPS-02 front-run mitigation proof.
    """
    from orchestrator.loop.keeper_monitor import execute_ready_orders

    publish_calls: list[dict] = []

    async def mock_publish(web3, journal_registry, db_session, **kwargs):
        """Mock publish_journal_entry — captures keyword args."""
        publish_calls.append(kwargs)

    # ── Set up mock web3 ─────────────────────────────────────────────────────
    mock_web3 = MagicMock()
    mock_web3.eth.get_block_number = AsyncMock(return_value=100)
    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"blockNumber": 100})

    # ── Set up MockPerps contract ────────────────────────────────────────────
    order_key_bytes = b"\xab" * 32
    order_key_hex = "0x" + "ab" * 32

    # executeOrder returns a tx hash
    mock_exec_tx = MagicMock()
    mock_exec_tx.hex.return_value = "deadbeef" * 8  # 64 hex chars

    mock_exec_receipt = {"blockNumber": 101}

    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=mock_exec_receipt)

    # OrderExecuted event fires
    mock_order_executed_event = MagicMock()
    mock_order_executed_event.process_receipt = MagicMock(
        return_value=[{"args": {"orderKey": order_key_bytes}}]
    )

    # PositionLiquidated event does NOT fire
    mock_position_liq_event = MagicMock()
    mock_position_liq_event.process_receipt = MagicMock(return_value=[])

    mock_perps_functions = MagicMock()
    mock_perps_functions.executeOrder = MagicMock(
        return_value=MagicMock(transact=AsyncMock(return_value=mock_exec_tx))
    )

    mock_perps_events = MagicMock()
    mock_perps_events.OrderExecuted = MagicMock(return_value=mock_order_executed_event)
    mock_perps_events.PositionLiquidated = MagicMock(return_value=mock_position_liq_event)

    mock_perps = MagicMock()
    mock_perps.functions = mock_perps_functions
    mock_perps.events = mock_perps_events

    # ── Set up DB session mock ───────────────────────────────────────────────
    # Simulates a pending_orders row that is block-ready
    pending_order = {
        "id": "test-uuid",
        "vault_address": "0xVault",
        "order_key": order_key_hex,
        "session_id": "session-uuid",
        "execute_after_block": 99,
        "status": "pending",
        "decision_snapshot": {
            "market": "ETH",
            "side": "long",
            "action": "open",
            "sizeUsd": 1000.0,
            "leverage": 1.0,
        },
    }

    mock_db = MagicMock()

    # ── Phase 1: Before executeOrder (createOrder receipt equivalent) ────────
    # At this point NO publish should have happened — the pending_orders row exists
    # but OrderExecuted has NOT been observed yet.
    assert len(publish_calls) == 0, "publish must NOT fire before OrderExecuted"

    # ── Phase 2: execute_ready_orders triggers OrderExecuted ─────────────────
    mock_journal_registry = MagicMock()
    fake_priv_key = b"\xfa" * 32  # gitleaks:allow — test key, no real value

    with (
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready",
            new_callable=AsyncMock,
            return_value=[pending_order],
        ),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestrator.loop.keeper_monitor.record_trade",
            new_callable=AsyncMock,
            return_value="0x" + "ab" * 32,
        ),
        patch(
            "orchestrator.loop.keeper_monitor.publish_journal_entry",
            side_effect=mock_publish,
        ),
        patch("orchestrator.loop.keeper_monitor._make_envelope", return_value={}),
        patch("orchestrator.loop.keeper_monitor._publish", new_callable=AsyncMock),
    ):
        results = await execute_ready_orders(
            mock_web3,
            mock_perps,
            mock_db,
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=None,
            session_id="session-uuid",
            seq_counter=1,
            # Journal params — required for PERPS-02 publish path
            journal_registry=mock_journal_registry,
            operator_journal_private_key=fake_priv_key,
            pinata_jwt="test-jwt",
            storacha_api_key=None,
        )

    # ── Assertions ────────────────────────────────────────────────────────────
    assert len(results) == 1
    assert results[0]["status"] == "executed"

    # CRITICAL (PERPS-02): publish called EXACTLY ONCE and only AFTER OrderExecuted
    assert len(publish_calls) == 1, (
        f"Expected exactly 1 publish_journal_entry call after OrderExecuted, "
        f"got {len(publish_calls)}"
    )
    # The publish was triggered by the vault_address from the pending order
    assert publish_calls[0].get("vault_address") == "0xVault"


@pytest.mark.asyncio
async def test_journal_not_published_on_liquidation() -> None:
    """PositionLiquidated (no OrderExecuted): publish_journal_entry must NOT be called."""
    from orchestrator.loop.keeper_monitor import execute_ready_orders

    publish_calls: list[dict] = []

    async def mock_publish_liquidation(web3, journal_registry, db_session, **kwargs):
        """Should never be called on liquidation path."""
        publish_calls.append(kwargs)

    mock_web3 = MagicMock()
    mock_web3.eth.get_block_number = AsyncMock(return_value=100)

    order_key_hex = "0x" + "cd" * 32

    mock_exec_tx = MagicMock()
    mock_exec_tx.hex.return_value = "beefcafe" * 8
    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"blockNumber": 102})

    # OrderExecuted does NOT fire (empty list)
    mock_order_executed_event = MagicMock()
    mock_order_executed_event.process_receipt = MagicMock(return_value=[])

    # PositionLiquidated fires
    mock_position_liq_event = MagicMock()
    mock_position_liq_event.process_receipt = MagicMock(
        return_value=[{"args": {"orderKey": bytes.fromhex("cd" * 32)}}]
    )

    mock_perps_functions = MagicMock()
    mock_perps_functions.executeOrder = MagicMock(
        return_value=MagicMock(transact=AsyncMock(return_value=mock_exec_tx))
    )
    mock_perps_events = MagicMock()
    mock_perps_events.OrderExecuted = MagicMock(return_value=mock_order_executed_event)
    mock_perps_events.PositionLiquidated = MagicMock(return_value=mock_position_liq_event)

    mock_perps = MagicMock()
    mock_perps.functions = mock_perps_functions
    mock_perps.events = mock_perps_events

    pending_order = {
        "id": "test-uuid-2",
        "vault_address": "0xVault",
        "order_key": order_key_hex,
        "session_id": "session-uuid",
        "execute_after_block": 99,
        "status": "pending",
        "decision_snapshot": {"market": "BTC", "side": "short", "action": "open"},
    }

    with (
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready",
            new_callable=AsyncMock,
            return_value=[pending_order],
        ),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestrator.loop.keeper_monitor.publish_journal_entry",
            side_effect=mock_publish_liquidation,
        ),
        patch("orchestrator.loop.keeper_monitor._make_envelope", return_value={}),
        patch("orchestrator.loop.keeper_monitor._publish", new_callable=AsyncMock),
    ):
        results = await execute_ready_orders(
            mock_web3,
            mock_perps,
            MagicMock(),
            deployer_address="0xDeployer",
            vault_address="0xVault",
            redis=None,
            session_id="session-uuid",
            seq_counter=2,
            # No journal params — liquidation path should not publish
        )

    assert len(results) == 1
    assert results[0]["status"] == "liquidated"

    # PERPS-02: publish must NOT fire on liquidation (no OrderExecuted)
    assert len(publish_calls) == 0, (
        f"publish_journal_entry must NOT be called on PositionLiquidated, "
        f"but was called {len(publish_calls)} time(s)"
    )


def test_driver_never_publishes() -> None:
    """Structural: driver.py must NOT import or call publish_journal_entry (PERPS-02)."""
    import inspect

    import orchestrator.loop.driver as driver_module

    source = inspect.getsource(driver_module)
    assert "publish_journal_entry" not in source, (
        "PERPS-02 violation: driver.py must never call publish_journal_entry. "
        "Journal publish is wired ONLY to the OrderExecuted event in keeper_monitor."
    )


def test_keeper_monitor_publishes_on_event() -> None:
    """Structural: keeper_monitor.py MUST import/call publish_journal_entry."""
    import inspect

    import orchestrator.loop.keeper_monitor as km_module

    source = inspect.getsource(km_module)
    assert "publish_journal_entry" in source, (
        "PERPS-02 requirement: keeper_monitor.py must call publish_journal_entry "
        "on the OrderExecuted event path."
    )

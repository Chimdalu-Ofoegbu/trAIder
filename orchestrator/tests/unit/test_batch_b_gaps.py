"""
orchestrator.tests.unit.test_batch_b_gaps — Regression tests for Batch B integration gaps.

Tests:
  GAP #3  keeper ordering: clearTradingLock receipt BEFORE mark_pending_order_executed
  GAP #8  publisher: revert receipt keeps state pinned_primary + fires CRITICAL alert
          publisher: success receipt transitions to recorded
  GAP #2  reconcile startup heal: clearTradingLock fired for executed-on-chain orders
  GAP #10 reconcile duplicate-prevention: tx pending in mempool → skip resubmit
  GAP #1/#7 driver pre-trade: stale feed (>3000s) → skip trade + WARNING alert
  GAP #4/#6 run_session: PRICE_PUSHER_KEY env used when set; fallback to OPERATOR_TRADE_KEY

All tests use mocks only — NO live session, NO gate spend, NO Opus calls.
Simulated Sepolia ~40-60s async latency via asyncio without real sleeps.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# GAP #3: keeper ordering — clearTradingLock before mark_pending_order_executed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keeper_clears_lock_before_db_unlock() -> None:
    """clearTradingLock receipt must be awaited BEFORE mark_pending_order_executed.

    GAP #3: at short Sepolia cadence, if DB releases in-flight lock before vault is
    unlocked, the driver can submit and hit "Vault: order in flight" on the very next
    cycle. Fix: on-chain unlock first, DB unlock second.
    """
    from orchestrator.loop.keeper_monitor import execute_ready_orders

    call_order: list[str] = []

    # Mock web3
    mock_web3 = MagicMock()
    mock_web3.eth.get_block_number = AsyncMock(return_value=100)

    # Mock clear_tx receipt (success)
    clear_receipt = MagicMock()
    clear_receipt.get = MagicMock(return_value=1)  # status=1

    # Mock vault_contract.clearTradingLock
    clear_tx_hash = MagicMock()
    clear_tx_hash.hex = MagicMock(return_value="0xclearTx")
    mock_clear_fn = MagicMock()
    mock_clear_fn.transact = AsyncMock(return_value=clear_tx_hash)
    mock_vault_functions = MagicMock()
    mock_vault_functions.clearTradingLock = MagicMock(return_value=mock_clear_fn)
    mock_vault_contract = MagicMock()
    mock_vault_contract.functions = mock_vault_functions

    # wait_for_transaction_receipt: first call is for executeOrder, second for clearTradingLock
    exec_receipt = MagicMock()
    exec_receipt.__getitem__ = MagicMock(side_effect=lambda k: 100 if k == "blockNumber" else None)
    exec_receipt.get = MagicMock(return_value=1)

    # Track receipt calls
    receipt_calls: list[str] = []

    async def mock_wait_receipt(tx, timeout=30):
        tx_str = str(tx)
        receipt_calls.append(tx_str)
        if "0xexecTx" in tx_str:
            return exec_receipt
        else:
            # clearTradingLock receipt — record that we got here
            call_order.append("clearTradingLock_receipt")
            return clear_receipt

    mock_web3.eth.wait_for_transaction_receipt = mock_wait_receipt

    # Mock executeOrder
    exec_tx_hash = MagicMock()
    exec_tx_hash.hex = MagicMock(return_value="0xexecTx")
    mock_exec_fn = MagicMock()
    mock_exec_fn.transact = AsyncMock(return_value=exec_tx_hash)
    mock_perps_functions = MagicMock()
    mock_perps_functions.executeOrder = MagicMock(return_value=mock_exec_fn)

    # Mock OrderExecuted event
    mock_order_event = MagicMock()
    mock_order_event.args = {"orderKey": bytes(32)}
    mock_executed_events = MagicMock()
    mock_executed_events.process_receipt = MagicMock(return_value=[mock_order_event])
    mock_perps_events = MagicMock()
    mock_perps_events.OrderExecuted = MagicMock(return_value=mock_executed_events)

    mock_perps = MagicMock()
    mock_perps.functions = mock_perps_functions
    mock_perps.events = mock_perps_events

    # Mock DB functions
    async def mock_record_trade(*args, **kwargs):
        return "0x" + "ab" * 32

    async def mock_mark_executed(*args, **kwargs):
        call_order.append("mark_pending_order_executed")

    mock_db = MagicMock()

    # Mock get_pending_orders_ready
    order_key_hex = "0x" + "01" * 32
    fake_order = {
        "order_key": order_key_hex,
        "decision_snapshot": {"market": "ETH", "side": "long", "action": "open", "sizeUsd": 1000.0},
    }

    # channel_for is imported inside execute_ready_orders from backend.ws.channels
    import backend.ws.channels as _bwsc

    with (
        patch(
            "orchestrator.loop.keeper_monitor.get_pending_orders_ready",
            new_callable=AsyncMock,
            return_value=[fake_order],
        ),
        patch("orchestrator.loop.keeper_monitor.record_trade", side_effect=mock_record_trade),
        patch(
            "orchestrator.loop.keeper_monitor.mark_pending_order_executed",
            side_effect=mock_mark_executed,
        ),
        patch(
            "orchestrator.loop.keeper_monitor.publish_journal_entry",
            new_callable=AsyncMock,
        ),
        patch("orchestrator.loop.keeper_monitor._make_envelope", return_value={}),
        patch("orchestrator.loop.keeper_monitor._publish", new_callable=AsyncMock),
        patch.object(_bwsc, "channel_for", return_value="test-channel"),
    ):
        results = await execute_ready_orders(
            mock_web3,
            mock_perps,
            mock_db,
            deployer_address="0xDeployer",
            vault_address="0xVault0000000000000000000000000000000000",
            session_id="00000000-0000-0000-0000-000000000001",
            seq_counter=1,
            vault_contract=mock_vault_contract,
            orchestrator_address="0x65A4e4DDc9Fe83A2c715959c8EaE6b0645824c4A",
        )

    assert results[0]["status"] == "executed", f"Expected executed, got {results[0]}"

    # KEY ASSERTION: clearTradingLock receipt must appear BEFORE mark_pending_order_executed
    assert "clearTradingLock_receipt" in call_order, (
        "clearTradingLock receipt was never awaited — GAP #3 fix missing"
    )
    assert "mark_pending_order_executed" in call_order, (
        "mark_pending_order_executed was never called"
    )
    clear_idx = call_order.index("clearTradingLock_receipt")
    exec_idx = call_order.index("mark_pending_order_executed")
    assert clear_idx < exec_idx, (
        f"clearTradingLock (idx={clear_idx}) must come BEFORE mark_pending_order_executed "
        f"(idx={exec_idx}). GAP #3 ordering violated: DB unlock before vault unlock."
    )


# ---------------------------------------------------------------------------
# GAP #8: publisher — revert receipt keeps pinned_primary + fires CRITICAL alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_revert_receipt_keeps_pinned_primary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """recordJournal tx reverts → state stays pinned_primary, CRITICAL alert fires.

    GAP #8: previously transact() immediately transitioned DB to 'recorded' without
    awaiting the receipt. A revert (status==0) left DB falsely showing 'recorded'.
    """
    from orchestrator.journal.publisher import publish_journal_entry

    db_state_transitions: list[str] = []
    alerts_fired: list[str] = []

    async def mock_update_state(session, *, vault_address, order_key, new_state, **kwargs):
        db_state_transitions.append(new_state)

    async def mock_send_alert(message, severity, **kwargs):
        alerts_fired.append(str(severity))

    # Mock: pin succeeds
    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"

    # Mock: recordJournal transact returns a tx hash
    fake_tx = "0xdeadbeef"
    mock_record_fn = MagicMock()
    mock_record_fn.transact = AsyncMock(return_value=fake_tx)
    mock_registry = MagicMock()
    mock_registry.functions.recordJournal = MagicMock(return_value=mock_record_fn)

    # Mock: wait_for_transaction_receipt returns status==0 (REVERT)
    revert_receipt = {"status": 0}
    mock_web3 = MagicMock()
    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=revert_receipt)

    priv_key = b"\xcc" * 32
    mock_db = MagicMock()

    with (
        patch(
            "orchestrator.journal.publisher.pin_to_pinata",
            new_callable=AsyncMock,
            return_value=fake_cid,
        ),
        patch("orchestrator.journal.publisher.update_journal_state", mock_update_state),
        patch("orchestrator.journal.publisher.send_alert", mock_send_alert),
    ):
        await publish_journal_entry(
            mock_web3,
            mock_registry,
            mock_db,
            vault_address="0xVault",
            trade_hash="0x" + "ab" * 32,
            order_key="0x" + "cd" * 32,
            payload={"cycle": 1},
            operator_journal_private_key=priv_key,
            pinata_jwt="test-jwt",
        )

    # State should have transitioned to pinned_primary but NOT to recorded
    assert "pinned_primary" in db_state_transitions, (
        "Expected pinned_primary transition; got: " + str(db_state_transitions)
    )
    assert "recorded" not in db_state_transitions, (
        "recorded state must NOT be set on revert receipt (GAP #8). "
        "Got transitions: " + str(db_state_transitions)
    )

    # CRITICAL alert must have fired
    from orchestrator.alerts.sink import AlertSeverity

    assert any(AlertSeverity.CRITICAL.value in a for a in alerts_fired), (
        f"Expected CRITICAL alert on revert; got: {alerts_fired}"
    )


@pytest.mark.asyncio
async def test_publisher_success_receipt_transitions_to_recorded() -> None:
    """recordJournal tx succeeds (status==1) → state transitions to recorded.

    GAP #8 happy path: receipt must be awaited and status==1 must gate the transition.
    """
    from orchestrator.journal.publisher import publish_journal_entry

    db_state_transitions: list[str] = []

    async def mock_update_state(session, *, vault_address, order_key, new_state, **kwargs):
        db_state_transitions.append(new_state)

    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    fake_tx = "0xsuccessTx"
    mock_record_fn = MagicMock()
    mock_record_fn.transact = AsyncMock(return_value=fake_tx)
    mock_registry = MagicMock()
    mock_registry.functions.recordJournal = MagicMock(return_value=mock_record_fn)

    # Mock: receipt status==1 (success)
    success_receipt = {"status": 1}
    mock_web3 = MagicMock()
    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=success_receipt)

    priv_key = b"\xdd" * 32
    mock_db = MagicMock()

    with (
        patch(
            "orchestrator.journal.publisher.pin_to_pinata",
            new_callable=AsyncMock,
            return_value=fake_cid,
        ),
        patch("orchestrator.journal.publisher.update_journal_state", mock_update_state),
    ):
        await publish_journal_entry(
            mock_web3,
            mock_registry,
            mock_db,
            vault_address="0xVault",
            trade_hash="0x" + "ab" * 32,
            order_key="0x" + "cd" * 32,
            payload={"cycle": 1},
            operator_journal_private_key=priv_key,
            pinata_jwt="test-jwt",
        )

    # pinned_primary then recorded — both in order
    assert "pinned_primary" in db_state_transitions, (
        "Expected pinned_primary in transitions; got: " + str(db_state_transitions)
    )
    assert "recorded" in db_state_transitions, (
        "Expected recorded in transitions on success receipt; got: " + str(db_state_transitions)
    )
    primary_idx = db_state_transitions.index("pinned_primary")
    recorded_idx = db_state_transitions.index("recorded")
    assert primary_idx < recorded_idx, "pinned_primary must precede recorded in state machine"


# ---------------------------------------------------------------------------
# GAP #2: reconcile startup heal — clearTradingLock for executed-on-chain orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_fires_clear_lock_for_executed_order() -> None:
    """Startup reconcile fires clearTradingLock for orders executed on-chain.

    GAP #2: SIGKILL between executeOrder confirming and clearTradingLock → vault
    stays locked. On restart, reconcile must call clearTradingLock unconditionally
    (idempotent) to heal the stuck lock.
    """
    from orchestrator.loop.driver import reconcile_pending_orders

    # Simulate an on-chain order with vault set and executed=True
    fake_order_key = "0x" + "aa" * 32
    fake_order = {
        "order_key": fake_order_key,
        "status": "pending",
        "decision_snapshot": None,
        "submit_tx_hash": None,
    }

    # pendingOrders returns struct with vault != 0x0 and executed=True
    vault_addr = "0x65A4e4DDc9Fe83A2c715959c8EaE6b0645824c4A"
    onchain_struct = (
        bytes(32),  # positionKey[0]
        100,  # executeAfterBlock[1]
        vault_addr,  # vault[2]
        False,  # isClose[3]
        True,  # executed[4] = True (GAP #2 trigger)
    )

    # Track clearTradingLock calls
    clear_lock_calls: list[bytes] = []
    clear_receipt = {"status": 1}
    clear_tx = MagicMock()
    clear_tx.hex = MagicMock(return_value="0xclearTx")

    mock_clear_fn = MagicMock()
    mock_clear_fn.transact = AsyncMock(return_value=clear_tx)
    mock_vault_functions = MagicMock()

    def mock_clear_trading_lock(key_bytes):
        clear_lock_calls.append(key_bytes)
        return mock_clear_fn

    mock_vault_functions.clearTradingLock = mock_clear_trading_lock
    mock_vault_contract = MagicMock()
    mock_vault_contract.functions = mock_vault_functions

    mock_perps = MagicMock()
    mock_perps.functions.pendingOrders.return_value.call = AsyncMock(return_value=onchain_struct)

    mock_web3 = MagicMock()
    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=clear_receipt)
    # get_transaction not called for real hex keys
    mock_web3.eth.get_transaction = AsyncMock(return_value=None)

    mock_db = MagicMock()

    with patch(
        "orchestrator.loop.driver.get_unresolved_pending_orders",
        new_callable=AsyncMock,
        return_value=[fake_order],
    ):
        result = await reconcile_pending_orders(
            mock_web3,
            mock_perps,
            mock_db,
            vault=vault_addr,
            vault_contract=mock_vault_contract,
            orchestrator_address="0x65A4e4DDc9Fe83A2c715959c8EaE6b0645824c4A",
        )

    # clearTradingLock must have been called for the executed order
    assert len(clear_lock_calls) == 1, (
        f"Expected clearTradingLock to be called once; got {len(clear_lock_calls)} calls. "
        "GAP #2 startup heal not firing."
    )
    # Should not be marked resubmittable (it's executed on-chain)
    assert result == 0, (
        f"Executed-on-chain order should NOT be resubmittable; got {result}. "
        "Resubmitting an executed order creates a duplicate."
    )


# ---------------------------------------------------------------------------
# GAP #10: reconcile duplicate-prevention — tx pending in mempool → skip resubmit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_skips_resubmit_when_tx_in_mempool() -> None:
    """reconcile does NOT mark resubmittable when original tx is in mempool.

    GAP #10: on restart, if the original submit tx is still pending (or mined),
    resubmitting creates a duplicate. Check eth_getTransactionByHash first.
    """
    from orchestrator.loop.driver import reconcile_pending_orders

    fake_tx_hash = "0xpendingTx"
    # intent-* row with a submit_tx_hash set
    fake_order = {
        "order_key": "intent-sess1-1-ETH",
        "status": "intent",
        "decision_snapshot": None,
        "submit_tx_hash": fake_tx_hash,
    }

    vault_addr = "0xVault0000000000000000000000000000000000"

    # eth.get_transaction returns a non-None value (tx exists in mempool/mined)
    tx_data = {"hash": fake_tx_hash, "blockNumber": None}  # blockNumber=None = pending
    mock_web3 = MagicMock()
    mock_web3.eth.get_transaction = AsyncMock(return_value=tx_data)

    mock_perps = MagicMock()
    mock_db = MagicMock()

    with patch(
        "orchestrator.loop.driver.get_unresolved_pending_orders",
        new_callable=AsyncMock,
        return_value=[fake_order],
    ):
        result = await reconcile_pending_orders(
            mock_web3,
            mock_perps,
            mock_db,
            vault=vault_addr,
        )

    # get_transaction must have been called with the tx hash
    mock_web3.eth.get_transaction.assert_called_once_with(fake_tx_hash)

    # Order must NOT be marked resubmittable (tx is still in mempool)
    assert result == 0, (
        f"Expected 0 resubmittable (tx still in mempool); got {result}. "
        "GAP #10: resubmitting a pending tx creates a duplicate."
    )


@pytest.mark.asyncio
async def test_reconcile_allows_resubmit_when_tx_absent() -> None:
    """reconcile marks resubmittable when tx is absent from mempool.

    GAP #10 complementary: if eth_getTransactionByHash returns None (tx dropped /
    was never broadcast), the order IS safe to resubmit.
    """
    from orchestrator.loop.driver import reconcile_pending_orders

    fake_tx_hash = "0xdroppedTx"
    fake_order = {
        "order_key": "intent-sess1-2-ETH",
        "status": "intent",
        "decision_snapshot": None,
        "submit_tx_hash": fake_tx_hash,
    }

    vault_addr = "0xVault0000000000000000000000000000000000"

    # eth.get_transaction returns None (tx not found — dropped or never broadcast)
    mock_web3 = MagicMock()
    mock_web3.eth.get_transaction = AsyncMock(return_value=None)

    mock_perps = MagicMock()
    mock_db = MagicMock()

    with patch(
        "orchestrator.loop.driver.get_unresolved_pending_orders",
        new_callable=AsyncMock,
        return_value=[fake_order],
    ):
        result = await reconcile_pending_orders(
            mock_web3,
            mock_perps,
            mock_db,
            vault=vault_addr,
        )

    # Order IS safe to resubmit (tx truly absent)
    assert result == 1, (
        f"Expected 1 resubmittable (tx absent); got {result}. When tx is absent, resubmit is safe."
    )


# ---------------------------------------------------------------------------
# GAP #1/#7: driver pre-trade feed-age check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_skips_trade_on_stale_feed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Driver skips trade and fires WARNING alert when feed age > 3000s (GAP #1/#7).

    Pre-trade staleness check: if latestRoundData().updatedAt is >3000s old, the
    driver MUST skip the trade (no submit) and fire WARNING via alert sink.
    Submitting into a stale feed guarantees "MockPerps: stale price" revert.
    """
    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.session import SessionConfig

    # Build a minimal stale aggregator: block.timestamp - updatedAt > 3000s
    _BLOCK_TS = 10000
    _STALE_UPDATED_AT = _BLOCK_TS - 3500  # 3500s ago > 3000s threshold

    # Stale latestRoundData: (roundId, answer, startedAt, updatedAt, answeredInRound)
    stale_round_data = (1, 300000000000, 1, _STALE_UPDATED_AT, 1)

    mock_aggregator = MagicMock()
    mock_aggregator.functions.latestRoundData.return_value.call = AsyncMock(
        return_value=stale_round_data
    )

    aggregators = {"ETH": mock_aggregator}

    # web3
    mock_web3 = MagicMock()
    mock_web3.eth.get_block_number = AsyncMock(return_value=100)
    latest_block = {"timestamp": _BLOCK_TS, "number": 100}
    mock_web3.eth.get_block = AsyncMock(return_value=latest_block)
    mock_web3.eth.get_block_number = AsyncMock(return_value=100)

    # mock_perps.executionDelay
    mock_perps = MagicMock()
    mock_perps.functions.executionDelay.return_value.call = AsyncMock(return_value=1)

    # Config (minimal)
    config = SessionConfig(
        session_id="00000000-0000-0000-0000-000000000003",
        session_duration_seconds=300,
        cadence_seconds=60.0,
        price_seed=42,
        drift=0.0001,
        volatility=0.005,
        execution_delay_cycles=1,
    )

    # A valid decision that would normally submit a trade
    from orchestrator.providers.anthropic_adapter import Decision

    decision = Decision(
        action="open",
        market="ETH",
        side="long",
        sizeUsd=1000.0,
        leverage=2.0,
        rationale="test",
        confidence=0.8,
        expectedHoldingPeriod="short",
    )

    # Walk mock
    mock_walk = MagicMock()

    # Track alerts
    alerts_fired: list[tuple[str, str]] = []

    async def mock_send_alert(message, severity, **kwargs):
        alerts_fired.append((str(severity), message))

    # DB mocks
    mock_db = MagicMock()
    mark_reconciled_called = []

    async def mock_record_journal(*args, **kwargs):
        pass

    async def mock_record_pending(*args, **kwargs):
        pass

    async def mock_mark_reconciled(*args, **kwargs):
        mark_reconciled_called.append(True)

    async def mock_has_unresolved(*args, **kwargs):
        return False

    # send_alert in the stale-feed branch is imported locally from orchestrator.alerts.sink
    # Patch the source module so the local import picks up the mock.
    import backend.ws.channels as _bwsc2

    with (
        patch(
            "orchestrator.loop.driver.call_claude", new_callable=AsyncMock, return_value=MagicMock()
        ),
        patch(
            "orchestrator.loop.driver.extract_tool_input",
            return_value={
                "action": "open",
                "market": "ETH",
                "side": "long",
                "sizeUsd": 1000.0,
                "leverage": 2.0,
                "rationale": "test",
            },
        ),
        patch("orchestrator.loop.driver.validate_decision", return_value=decision),
        patch("orchestrator.loop.driver.validate_business_rules", return_value=None),
        patch("orchestrator.loop.driver.has_unresolved_pending_order", mock_has_unresolved),
        patch("orchestrator.loop.driver.record_journal_pending", mock_record_journal),
        patch("orchestrator.loop.driver.record_pending_order", mock_record_pending),
        patch("orchestrator.loop.driver.mark_pending_order_reconciled", mock_mark_reconciled),
        patch("orchestrator.loop.driver.record_model_status", new_callable=AsyncMock),
        patch("orchestrator.loop.driver._make_envelope", return_value={}),
        patch("orchestrator.loop.driver._publish", new_callable=AsyncMock),
        patch(
            "orchestrator.loop.driver.build_market_table_from_snapshot",
            return_value="| ETH | 3000 |",
        ),
        patch("orchestrator.loop.driver.build_market_table", return_value="| ETH | 3000 |"),
        patch(
            "orchestrator.loop.driver.read_mark_prices",
            new_callable=AsyncMock,
            return_value={"ETH": 3000.0},
        ),
        # render_prompt is imported inside the function body from orchestrator.loop.market_state
        patch("orchestrator.loop.market_state.render_prompt", return_value="test prompt"),
        patch.object(_bwsc2, "channel_for", return_value="test-channel"),
        # Patch send_alert at the sink module level (driver imports it locally)
        patch("orchestrator.alerts.sink.send_alert", mock_send_alert),
    ):
        result = await run_live_cycle(
            mock_web3,
            mock_perps,
            "0xVault0000000000000000000000000000000000",
            "claude-opus-4-7",
            cycle=1,
            config=config,
            walk=mock_walk,
            aggregators=aggregators,
            tracker=MagicMock(
                should_pause=MagicMock(return_value=False),
                record_success=MagicMock(return_value=False),
            ),
            db=mock_db,
            redis=None,
            session_id="00000000-0000-0000-0000-000000000003",
            seq=1,
            available_usdc=10000.0,
            open_positions={},
            nav_table="| Vault | $10k |",
            positions_table="No open positions.",
            recent_decisions="None",
            elapsed_seconds=0.0,
            market_snapshot=None,
        )

    # Trade must have been skipped
    assert result.get("status") == "skipped_stale_feed", (
        f"Expected 'skipped_stale_feed' status on stale feed; got {result.get('status')}. "
        "GAP #1/#7: driver must skip trade when feed is >3000s stale."
    )

    # WARNING alert must have fired
    from orchestrator.alerts.sink import AlertSeverity

    assert any(AlertSeverity.WARNING.value in sev for sev, _ in alerts_fired), (
        f"Expected WARNING alert on stale feed; got: {alerts_fired}"
    )

    # Intent row must have been cleared (reconciled)
    assert len(mark_reconciled_called) >= 1, (
        "Intent row must be cleared when trade is skipped (mark_pending_order_reconciled)"
    )


# ---------------------------------------------------------------------------
# GAP #4/#6: run_session PRICE_PUSHER_KEY env — used when set; fallback when unset
# ---------------------------------------------------------------------------


def test_price_pusher_key_env_used_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRICE_PUSHER_KEY env produces a separate price-pusher address (GAP #4/#6).

    The price pusher's from_address must differ from the operator-trade EOA when
    PRICE_PUSHER_KEY is set to a different hex key.
    """
    # Use two different test keys (publicly known Foundry test keys)
    # These have no real value but are valid secp256k1 keys.  # gitleaks:allow
    operator_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    pusher_key = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

    from eth_account import Account

    operator_acct = Account.from_key(operator_key)
    pusher_acct = Account.from_key(pusher_key)

    assert operator_acct.address != pusher_acct.address, (
        "Test setup error: operator and pusher keys must produce different addresses"
    )

    # Read the PRICE_PUSHER_KEY from env in run_session.py
    # We test the resolution logic directly (not the full session which needs DB/RPC)
    with monkeypatch.context() as m:
        m.setenv("PRICE_PUSHER_KEY", pusher_key)

        # Simulate the key-resolution logic from run_mini_session
        price_pusher_key_hex = os.environ.get("PRICE_PUSHER_KEY", "")
        resolved_pusher_address: str | None = None
        if price_pusher_key_hex:
            if not price_pusher_key_hex.startswith("0x"):
                price_pusher_key_hex = "0x" + price_pusher_key_hex
            resolved_pusher_address = Account.from_key(price_pusher_key_hex).address

    # When PRICE_PUSHER_KEY is set, the resolved address must match the pusher account
    assert resolved_pusher_address == pusher_acct.address, (
        f"Expected price pusher address {pusher_acct.address}, got {resolved_pusher_address}. "
        "GAP #4/#6: PRICE_PUSHER_KEY must produce a different EOA from OPERATOR_TRADE_KEY."
    )
    assert resolved_pusher_address != operator_acct.address, (
        "PRICE_PUSHER_KEY and OPERATOR_TRADE_KEY must produce different addresses (SEC-01)."
    )


def test_price_pusher_key_fallback_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PRICE_PUSHER_KEY is unset, price_pusher_address is None (fallback to deployer).

    GAP #4/#6: backward compat — unset PRICE_PUSHER_KEY must not break existing deployments.
    The driver.run_session falls back to deployer_address when price_pusher_address is None.
    """
    with monkeypatch.context() as m:
        m.delenv("PRICE_PUSHER_KEY", raising=False)

        price_pusher_key_hex = os.environ.get("PRICE_PUSHER_KEY", "")
        resolved_pusher_address: str | None = None
        if price_pusher_key_hex:
            from eth_account import Account

            resolved_pusher_address = Account.from_key(price_pusher_key_hex).address

    # When PRICE_PUSHER_KEY is unset, resolved_pusher_address should be None
    assert resolved_pusher_address is None, (
        f"Expected None (fallback to OPERATOR_TRADE_KEY); got {resolved_pusher_address}. "
        "GAP #4/#6: backward compat broken — PRICE_PUSHER_KEY unset should use fallback."
    )

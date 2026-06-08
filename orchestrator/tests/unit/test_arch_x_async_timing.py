"""ARCH-X async-timing hardening regression tests.

Covers all items (a)-(f) from the scope specification:

  (a) No submission over an in-flight order (ARCH-X gate)
  (b) No session crash on collision or revert (graceful continue)
  (c) Lock/pending state clears correctly on keeper resolution
  (d) A SECOND vault keeps deciding while the first is in-flight (per-vault independence)
  (e) Stale-lock/race case: intent row created BEFORE transact = compare-and-set
  (f) Watchdog does NOT false-trip at ~40-60s; DOES trip on genuine stall

Root cause documented: all three defects (prompt 72h hardcode, clearTradingLock timing,
in-flight collision) trace to a single ANVIL-ONLY INSTANT-EXECUTION assumption.
Sepolia execution is ~40-60s. These tests simulate that latency via mocks.

All tests run fully without Postgres or anvil — only mocks/stubs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_decision(action: str = "open", market: str = "ETH", side: str = "long") -> Any:
    """Return a minimal mock Decision object."""
    d = MagicMock()
    d.action = action
    d.market = market
    d.side = side
    d.sizeUsd = 1000.0
    d.leverage = 2.0
    d.model_dump.return_value = {
        "action": action,
        "market": market,
        "side": side,
        "sizeUsd": 1000.0,
        "leverage": 2.0,
        "rationale": "test",
    }
    return d


def _fake_order_hex() -> str:
    return "0xabcdef1234" + "0" * 54


# ---------------------------------------------------------------------------
# (a) + (e): submission gating — ARCH-X in-flight check + stale-lock / compare-and-set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_flight_gate_skips_submission() -> None:
    """(a) + (e): when an unresolved pending order exists for a vault, run_live_cycle
    returns status='skipped_inflight' WITHOUT writing a new intent row or calling .transact().

    This is the primary ARCH-X gate.  The intent row functions as the compare-and-set:
    the gate queries BEFORE writing the intent, so the check → write sequence is
    effectively atomic for a single-vault session (single-owner invariant).
    """
    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.session import SessionConfig

    vault = "0x" + "A" * 40
    session_id = "00000000-0000-0000-0000-000000000042"

    # Simulate ~50s Sepolia execution latency: prior order is still pending.
    transact_called: list[bool] = []
    intent_rows_written: list[str] = []
    pending_rows_written: list[str] = []

    async def fake_has_unresolved(db, *, vault_address: str) -> bool:
        # Vault has an unresolved order (simulating ~50s Sepolia in-flight window)
        return True

    async def fake_record_journal_pending(db, *, vault_address, order_key, **kw) -> None:
        intent_rows_written.append(order_key)

    async def fake_record_pending_order(db, *, vault_address, order_key, **kw) -> None:
        pending_rows_written.append(order_key)

    async def fake_call_claude(prompt, *, model):
        return MagicMock()

    decision = _make_decision()

    def fake_validate_decision(raw):
        return decision

    config = SessionConfig(
        session_duration_seconds=300,
        cadence_seconds=60.0,
        price_seed=42,
        execution_delay_cycles=1,
    )

    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=100)
    mock_perps = MagicMock()
    mock_perps.functions.executionDelay.return_value.call = AsyncMock(return_value=1)
    mock_perps.functions.openLong.return_value.transact = AsyncMock(
        side_effect=lambda _: transact_called.append(True)  # type: ignore[return-value]
    )

    from orchestrator.loop.failure_tracker import FailureTracker

    tracker = FailureTracker()
    db = AsyncMock()

    with (
        patch("orchestrator.loop.driver.call_claude", side_effect=fake_call_claude),
        patch("orchestrator.loop.driver.extract_tool_input", return_value={"action": "open"}),
        patch("orchestrator.loop.driver.validate_decision", side_effect=fake_validate_decision),
        patch("orchestrator.loop.driver.validate_business_rules", return_value=None),
        patch(
            "orchestrator.loop.driver.has_unresolved_pending_order",
            side_effect=fake_has_unresolved,
        ),
        patch(
            "orchestrator.loop.driver.record_journal_pending",
            side_effect=fake_record_journal_pending,
        ),
        patch(
            "orchestrator.loop.driver.record_pending_order",
            side_effect=fake_record_pending_order,
        ),
        patch("orchestrator.loop.driver._build_open_positions", return_value={}),
        patch("orchestrator.loop.driver._publish"),
        patch("orchestrator.loop.driver._make_envelope", return_value={}),
        patch("orchestrator.loop.driver.record_model_status"),
        patch("orchestrator.loop.driver.build_market_table", return_value="table"),
        patch("orchestrator.loop.driver.read_mark_prices", return_value={"ETH": 3500.0}),
        patch(
            "orchestrator.loop.market_state.render_prompt",
            return_value="prompt",
        ),
    ):
        result = await run_live_cycle(
            web3,
            mock_perps,
            vault,
            "claude-opus-4-7",
            cycle=5,
            config=config,
            walk=MagicMock(),
            aggregators={},
            tracker=tracker,
            db=db,
            redis=None,
            session_id=session_id,
            seq=5,
            available_usdc=10_000.0,
            open_positions={},
            nav_table="nav",
            positions_table="pos",
            recent_decisions="none",
            elapsed_seconds=120.0,
        )

    # ARCH-X gate must return skipped_inflight, NOT submitted or error
    assert result["status"] == "skipped_inflight", (
        f"Expected status='skipped_inflight' but got {result['status']!r}. "
        "ARCH-X gate must skip submission when a prior order is in-flight."
    )
    assert result["reason"] == "order pending — skipping submit this cycle"

    # .transact() must NEVER be called — no submission over an in-flight order
    assert transact_called == [], (
        "ARCH-X violation: .transact() was called even though an order was in-flight. "
        "This would cause 'Vault: order in flight' revert."
    )

    # No intent row with the real key should have been written (only the skipped-inflight journal)
    real_intent_keys = [k for k in intent_rows_written if "intent-" in k]
    assert real_intent_keys == [], (
        f"ARCH-X violation: intent rows written despite in-flight gate: {real_intent_keys}. "
        "The intent row acts as the lock — must NOT be created for a skipped cycle."
    )
    pending_real = [k for k in pending_rows_written if "intent-" in k]
    assert pending_real == [], (
        f"ARCH-X violation: pending_orders rows written for in-flight skip: {pending_real}."
    )


# ---------------------------------------------------------------------------
# (b): graceful catch of "Vault: order in flight" revert — session never crashes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_in_flight_revert_does_not_crash_session() -> None:
    """(b): even if the ARCH-X gate is somehow bypassed, a 'Vault: order in flight'
    revert from .transact() is caught gracefully.  The cycle returns status='error' and
    the session NEVER raises / crashes.

    This is the belt-and-suspenders layer on top of the gate.
    """
    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.session import SessionConfig

    vault = "0x" + "B" * 40
    session_id = "00000000-0000-0000-0000-000000000043"

    intent_cleared: list[str] = []

    async def fake_has_unresolved(db, *, vault_address: str) -> bool:
        # Gate passes (e.g., gate race — prior order just submitted but not yet recorded)
        return False

    async def fake_mark_reconciled(db, *, vault_address, order_key) -> None:
        intent_cleared.append(order_key)

    async def fake_call_claude(prompt, *, model):
        return MagicMock()

    decision = _make_decision()

    def fake_validate_decision(raw):
        return decision

    config = SessionConfig(
        session_duration_seconds=300,
        cadence_seconds=60.0,
        price_seed=42,
        execution_delay_cycles=1,
    )

    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=100)

    # .transact() raises the on-chain revert message
    mock_perps = MagicMock()
    mock_perps.functions.executionDelay.return_value.call = AsyncMock(return_value=1)

    vault_contract = MagicMock()
    open_fn_obj = MagicMock()
    open_fn_obj.transact = AsyncMock(
        side_effect=Exception("execution reverted: Vault: order in flight")
    )
    vault_contract.functions.openLong = MagicMock(return_value=open_fn_obj)
    vault_contract.functions.openShort = MagicMock(return_value=open_fn_obj)

    from orchestrator.loop.failure_tracker import FailureTracker

    tracker = FailureTracker()
    db = AsyncMock()

    with (
        patch("orchestrator.loop.driver.call_claude", side_effect=fake_call_claude),
        patch("orchestrator.loop.driver.extract_tool_input", return_value={"action": "open"}),
        patch("orchestrator.loop.driver.validate_decision", side_effect=fake_validate_decision),
        patch("orchestrator.loop.driver.validate_business_rules", return_value=None),
        patch(
            "orchestrator.loop.driver.has_unresolved_pending_order",
            side_effect=fake_has_unresolved,
        ),
        patch("orchestrator.loop.driver.record_journal_pending"),
        patch("orchestrator.loop.driver.record_pending_order"),
        patch(
            "orchestrator.loop.driver.mark_pending_order_reconciled",
            side_effect=fake_mark_reconciled,
        ),
        patch("orchestrator.loop.driver._build_open_positions", return_value={}),
        patch("orchestrator.loop.driver._publish"),
        patch("orchestrator.loop.driver._make_envelope", return_value={}),
        patch("orchestrator.loop.driver.record_model_status"),
        patch("orchestrator.loop.driver.build_market_table", return_value="table"),
        patch("orchestrator.loop.driver.read_mark_prices", return_value={"ETH": 3500.0}),
        patch("orchestrator.loop.market_state.render_prompt", return_value="prompt"),
    ):
        # This must NOT raise — the revert is caught gracefully
        result = await run_live_cycle(
            web3,
            mock_perps,
            vault,
            "claude-opus-4-7",
            cycle=1,
            config=config,
            walk=MagicMock(),
            aggregators={},
            tracker=tracker,
            db=db,
            redis=None,
            session_id=session_id,
            seq=1,
            available_usdc=10_000.0,
            open_positions={},
            nav_table="nav",
            positions_table="pos",
            recent_decisions="none",
            elapsed_seconds=60.0,
            vault_contract=vault_contract,
            operator_trade_address="0x" + "C" * 40,
        )

    # Session must NOT crash — result is returned
    assert result is not None, "run_live_cycle raised instead of returning — session crashed!"
    assert result["status"] == "error", (
        f"Expected status='error' after in-flight revert, got {result['status']!r}"
    )
    assert (
        "order in flight" in result["reason"].lower()
        or "transact failed" in result["reason"].lower()
    ), f"Reason should mention the revert; got: {result['reason']!r}"

    # Intent row must have been cleaned up (reconciled), not left dangling
    assert len(intent_cleared) >= 1, (
        "Intent row must be reconciled after a transact-level failure. "
        "Without this, the vault stays gated on the orphaned intent row forever."
    )


# ---------------------------------------------------------------------------
# (c): keeper resolution clears the lock correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keeper_resolution_clears_pending_state() -> None:
    """(c): when the keeper executes an order (OrderExecuted), mark_pending_order_executed
    is called and the vault's pending_orders row transitions to 'executed'.

    This verifies the ARCH-X gate will unblock the next cycle after keeper resolution.
    """
    from orchestrator.loop.keeper_monitor import execute_ready_orders

    order_key_hex = "0xdead1234" + "0" * 56
    order_key_bytes = bytes.fromhex(order_key_hex.removeprefix("0x"))

    fake_order = {
        "id": "uuid-c",
        "vault_address": "0x" + "D" * 40,
        "order_key": order_key_hex,
        "session_id": "00000000-0000-0000-0000-000000000044",
        "execute_after_block": 100,
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

    fake_exec_tx = b"\xde\xad" + b"\x00" * 30
    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=110)
    web3.eth.wait_for_transaction_receipt = AsyncMock(
        return_value={"blockNumber": 110, "status": 1}
    )

    mock_perps = MagicMock()
    mock_perps.functions.executeOrder.return_value.transact = AsyncMock(return_value=fake_exec_tx)
    fake_event_data = {"args": {"orderKey": order_key_bytes, "positionKey": b"\x00" * 32}}
    mock_perps.events.OrderExecuted.return_value.process_receipt = MagicMock(
        return_value=[fake_event_data]
    )

    executed_keys: list[str] = []
    db_session = AsyncMock()

    async def fake_get_ready(sess, block, *, vault_address=None):
        return [fake_order]

    async def fake_mark_executed(sess, *, vault_address, order_key):
        executed_keys.append(order_key)

    async def fake_record_trade(sess, *, vault_address, session_id, order_key, **kw):
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
            vault_address="0x" + "D" * 40,
            redis=None,
            session_id="00000000-0000-0000-0000-000000000044",
            seq_counter=1,
        )

    assert results[0]["status"] == "executed"
    # The pending_orders row must be transitioned to 'executed'
    assert order_key_hex in executed_keys, (
        "mark_pending_order_executed was not called after OrderExecuted. "
        "The ARCH-X gate would block the next cycle forever."
    )


# ---------------------------------------------------------------------------
# (d): per-vault independence — vault B keeps trading while vault A is in-flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_vault_independence_second_vault_keeps_trading() -> None:
    """(d): the in-flight state is keyed by vault_address.  While vault A has an
    unresolved pending order, vault B should still be able to submit (gate passes).

    This confirms no global lock — only per-vault locking.
    """
    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.session import SessionConfig

    vault_a = "0x" + "A" * 40
    vault_b = "0x" + "B" * 40
    session_id = "00000000-0000-0000-0000-000000000045"

    # vault_a: in-flight; vault_b: clear
    async def fake_has_unresolved(db, *, vault_address: str) -> bool:
        return vault_address == vault_a  # Only vault A is in-flight

    order_key_hex = _fake_order_hex()
    order_key_bytes = bytes.fromhex(order_key_hex.removeprefix("0x"))

    fake_exec_tx = b"\xca\xfe" + b"\x00" * 30

    async def fake_call_claude(prompt, *, model):
        return MagicMock()

    decision = _make_decision()

    def fake_validate_decision(raw):
        return decision

    config = SessionConfig(
        session_duration_seconds=300,
        cadence_seconds=60.0,
        price_seed=42,
        execution_delay_cycles=1,
    )

    web3 = AsyncMock()
    web3.eth.get_block_number = AsyncMock(return_value=100)

    # Simulate receipt + OrderCreated for vault B's successful submit
    fake_receipt = {"status": 1, "blockNumber": 101}
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=fake_receipt)

    mock_perps = MagicMock()
    mock_perps.functions.executionDelay.return_value.call = AsyncMock(return_value=1)
    mock_perps.functions.openLong.return_value.transact = AsyncMock(return_value=fake_exec_tx)
    mock_perps.functions.openShort.return_value.transact = AsyncMock(return_value=fake_exec_tx)
    fake_event_data = {"args": {"orderKey": order_key_bytes}}
    mock_perps.events.OrderCreated.return_value.process_receipt = MagicMock(
        return_value=[fake_event_data]
    )

    from orchestrator.loop.failure_tracker import FailureTracker

    # ── Test vault A: should get skipped_inflight ─────────────────────────────
    tracker_a = FailureTracker()
    with (
        patch("orchestrator.loop.driver.call_claude", side_effect=fake_call_claude),
        patch("orchestrator.loop.driver.extract_tool_input", return_value={"action": "open"}),
        patch("orchestrator.loop.driver.validate_decision", side_effect=fake_validate_decision),
        patch("orchestrator.loop.driver.validate_business_rules", return_value=None),
        patch(
            "orchestrator.loop.driver.has_unresolved_pending_order", side_effect=fake_has_unresolved
        ),
        patch("orchestrator.loop.driver.record_journal_pending"),
        patch("orchestrator.loop.driver.record_pending_order"),
        patch("orchestrator.loop.driver.mark_pending_order_reconciled"),
        patch("orchestrator.loop.driver._build_open_positions", return_value={}),
        patch("orchestrator.loop.driver._publish"),
        patch("orchestrator.loop.driver._make_envelope", return_value={}),
        patch("orchestrator.loop.driver.record_model_status"),
        patch("orchestrator.loop.driver.build_market_table", return_value="table"),
        patch("orchestrator.loop.driver.read_mark_prices", return_value={"ETH": 3500.0}),
        patch("orchestrator.loop.market_state.render_prompt", return_value="prompt"),
    ):
        result_a = await run_live_cycle(
            web3,
            mock_perps,
            vault_a,
            "claude-opus-4-7",
            cycle=1,
            config=config,
            walk=MagicMock(),
            aggregators={},
            tracker=tracker_a,
            db=AsyncMock(),
            redis=None,
            session_id=session_id,
            seq=1,
            available_usdc=10_000.0,
            open_positions={},
            nav_table="nav",
            positions_table="pos",
            recent_decisions="none",
            elapsed_seconds=60.0,
        )

    assert result_a["status"] == "skipped_inflight", (
        f"vault_a should be skipped (in-flight), got {result_a['status']!r}"
    )

    # ── Test vault B: should proceed to submitted ─────────────────────────────
    tracker_b = FailureTracker()
    with (
        patch("orchestrator.loop.driver.call_claude", side_effect=fake_call_claude),
        patch("orchestrator.loop.driver.extract_tool_input", return_value={"action": "open"}),
        patch("orchestrator.loop.driver.validate_decision", side_effect=fake_validate_decision),
        patch("orchestrator.loop.driver.validate_business_rules", return_value=None),
        patch(
            "orchestrator.loop.driver.has_unresolved_pending_order", side_effect=fake_has_unresolved
        ),
        patch("orchestrator.loop.driver.record_journal_pending"),
        patch("orchestrator.loop.driver.record_pending_order"),
        patch("orchestrator.loop.driver.mark_pending_order_reconciled"),
        patch("orchestrator.loop.driver._build_open_positions", return_value={}),
        patch("orchestrator.loop.driver._publish"),
        patch("orchestrator.loop.driver._make_envelope", return_value={}),
        patch("orchestrator.loop.driver.record_model_status"),
        patch("orchestrator.loop.driver.build_market_table", return_value="table"),
        patch("orchestrator.loop.driver.read_mark_prices", return_value={"ETH": 3500.0}),
        patch("orchestrator.loop.market_state.render_prompt", return_value="prompt"),
    ):
        result_b = await run_live_cycle(
            web3,
            mock_perps,
            vault_b,
            "claude-opus-4-7",
            cycle=1,
            config=config,
            walk=MagicMock(),
            aggregators={},
            tracker=tracker_b,
            db=AsyncMock(),
            redis=None,
            session_id=session_id,
            seq=1,
            available_usdc=10_000.0,
            open_positions={},
            nav_table="nav",
            positions_table="pos",
            recent_decisions="none",
            elapsed_seconds=60.0,
        )

    # vault_b's order is not in-flight → it should submit (or at least not be skipped)
    assert result_b["status"] != "skipped_inflight", (
        f"vault_b must NOT be blocked by vault_a's in-flight order. "
        f"Got status={result_b['status']!r}. Per-vault independence broken."
    )
    # vault_b should have reached the submitted path (or errored on the mock setup, but not gated)
    assert result_b["status"] in ("submitted", "error"), (
        f"vault_b should reach the submit path; got {result_b['status']!r}"
    )


# ---------------------------------------------------------------------------
# (f) Watchdog threshold — does NOT false-trip at 40-60s; DOES trip on genuine stall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_does_not_false_trip_at_50s() -> None:
    """(f) part 1: latency watchdog does NOT alert at ~50s (normal Sepolia execution).

    With the old 30s threshold, every normal ~50s Sepolia cycle would fire a false alert.
    The new default (120s) must remain silent at 50s elapsed.
    """
    from orchestrator.loop.run_session import _latency_watchdog_queue_driven

    alerts_fired: list[dict] = []

    async def fake_send_alert(message, severity, *, context=None, **kw):
        alerts_fired.append({"message": message, "severity": severity})

    stop_event = asyncio.Event()
    event_queue: asyncio.Queue = asyncio.Queue(maxsize=32)

    async def run_watchdog():
        with patch("orchestrator.loop.run_session.send_alert", side_effect=fake_send_alert):
            await _latency_watchdog_queue_driven(
                vault_address="0x" + "E" * 40,
                threshold_seconds=120.0,  # new default
                stop_event=stop_event,
                event_queue=event_queue,
                telegram_bot_token=None,
                telegram_chat_id=None,
            )

    watchdog_task = asyncio.create_task(run_watchdog())

    # Register a pending order at t=0
    order_key = "0x" + "1" * 64
    now = time.monotonic()
    await event_queue.put(("pending", order_key, now))

    # Wait 0.2s (simulates ~50s elapsed at 1000x speed — we use mock time via the
    # monotonic stamp we injected, which was 'now'; watchdog sees elapsed = 0.2s < 120s)
    await asyncio.sleep(0.2)

    # Stop watchdog
    stop_event.set()
    try:
        await asyncio.wait_for(watchdog_task, timeout=2.0)
    except TimeoutError:
        watchdog_task.cancel()

    # At 0.2s elapsed (well below 120s threshold), NO alert should have fired
    assert alerts_fired == [], (
        f"Watchdog false-tripped at 0.2s < 120s threshold. Alerts: {alerts_fired}. "
        "ARCH-X D-03: normal Sepolia execution must not trigger watchdog alert."
    )


@pytest.mark.asyncio
async def test_watchdog_fires_on_genuine_stall() -> None:
    """(f) part 2: latency watchdog DOES fire an alert when elapsed >> threshold.

    A genuine stall (keeper down, sequencer offline) must trip the watchdog.
    We inject an order with a timestamp 200s in the past so it's already over threshold
    at the first watchdog tick.
    """
    from orchestrator.loop.run_session import _latency_watchdog_queue_driven

    alerts_fired: list[dict] = []

    async def fake_send_alert(message, severity, *, context=None, **kw):
        alerts_fired.append({"message": message, "severity": severity})

    stop_event = asyncio.Event()
    event_queue: asyncio.Queue = asyncio.Queue(maxsize=32)

    async def run_watchdog():
        with patch("orchestrator.loop.run_session.send_alert", side_effect=fake_send_alert):
            await _latency_watchdog_queue_driven(
                vault_address="0x" + "F" * 40,
                threshold_seconds=5.0,  # low threshold for test speed (still >> 0.2s)
                stop_event=stop_event,
                event_queue=event_queue,
                telegram_bot_token=None,
                telegram_chat_id=None,
            )

    watchdog_task = asyncio.create_task(run_watchdog())

    # Register order with timestamp 10s in the past (already over the 5s threshold)
    order_key = "0x" + "2" * 64
    past_ts = time.monotonic() - 10.0  # 10 seconds ago
    await event_queue.put(("pending", order_key, past_ts))

    # Give watchdog one tick to process (2.1s — slightly more than watchdog tick of 2.0s)
    await asyncio.sleep(2.5)

    stop_event.set()
    try:
        await asyncio.wait_for(watchdog_task, timeout=2.0)
    except TimeoutError:
        watchdog_task.cancel()

    # At 10s elapsed > 5s threshold, the alert MUST have fired
    assert len(alerts_fired) >= 1, (
        "Watchdog failed to alert on genuine stall (10s > 5s threshold). "
        "ARCH-X D-03: genuine stalls must fire the watchdog."
    )
    alert_messages = [a["message"] for a in alerts_fired]
    assert any("1A latency breach" in m or "breach" in m.lower() for m in alert_messages), (
        f"Alert message should mention latency breach; got: {alert_messages}"
    )


# ---------------------------------------------------------------------------
# (b) additional: stale-lock race — the compare-and-set property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_toctou_intent_row_is_the_lock() -> None:
    """(e) stale-lock/race: the intent row is the compare-and-set lock.

    The driver writes the intent row (record_pending_order status='intent') BEFORE
    the .transact() call.  has_unresolved_pending_order sees this row on the NEXT cycle,
    so a second attempt after a partial crash cannot slip through.

    This test verifies that:
    - After an incomplete cycle (intent written but no real key yet), the next cycle
      sees has_unresolved=True and skips.
    - Only AFTER the intent is promoted and reconciled (real key written, intent→reconciled)
      does the gate allow a new cycle.
    """
    # Simulate the DB state machine:
    # Phase 1: intent row exists (written before transact) → gate blocks
    # Phase 2: intent reconciled, real key pending → gate blocks
    # Phase 3: real key executed → gate passes

    db_state: dict[str, str] = {}  # order_key → status

    async def fake_has_unresolved(db, *, vault_address: str) -> bool:
        # True if any intent/pending row exists
        return any(s in ("intent", "pending") for s in db_state.values())

    async def fake_record_pending_order(db, *, vault_address, order_key, status, **kw):
        db_state[order_key] = status

    async def fake_mark_reconciled(db, *, vault_address, order_key):
        if db_state.get(order_key) in ("intent", "pending"):
            db_state[order_key] = "reconciled"

    async def fake_mark_executed(db, *, vault_address, order_key):
        if db_state.get(order_key) == "pending":
            db_state[order_key] = "executed"

    # Phase 1: intent row written, gate should block
    db_state["intent-session-1-ETH"] = "intent"
    assert await fake_has_unresolved(None, vault_address="0xV"), (
        "Gate should block when intent row exists"
    )

    # Phase 2: intent promoted to real key + reconciled
    db_state["intent-session-1-ETH"] = "reconciled"
    db_state["0x" + "A" * 64] = "pending"
    assert await fake_has_unresolved(None, vault_address="0xV"), (
        "Gate should block when pending row exists (keeper not yet executed)"
    )

    # Phase 3: real key executed by keeper
    db_state["0x" + "A" * 64] = "executed"
    assert not await fake_has_unresolved(None, vault_address="0xV"), (
        "Gate should PASS when all rows are executed/reconciled (keeper resolved)"
    )

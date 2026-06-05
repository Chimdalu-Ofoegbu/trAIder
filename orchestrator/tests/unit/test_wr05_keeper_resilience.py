"""Unit tests for WR-05: keeper survives a transient exception and keeps running.

Regression test:
  - Monkeypatch a transient exception on one poll iteration.
  - Assert the keeper logs it (WARNING) and KEEPS RUNNING (processes a subsequent ready order).
  - Assert CancelledError propagates cleanly (cancellation is not swallowed).
"""

from __future__ import annotations

import asyncio
import logging

import pytest


@pytest.mark.asyncio
async def test_keeper_survives_transient_exception_and_keeps_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A transient exception in execute_ready_orders must not kill the keeper.

    WR-05 regression test.  Previously run_keeper_monitor had no outer try/except,
    so an exception from get_block_number() or get_pending_orders_ready() would
    terminate the asyncio.Task silently.
    """
    from unittest.mock import AsyncMock, patch

    from orchestrator.loop.keeper_monitor import run_keeper_monitor

    stop_event = asyncio.Event()

    call_count = 0
    results_from_successful_call: list[dict] = []

    async def fake_execute_ready_orders(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call raises — simulates transient web3 failure
            raise RuntimeError("Simulated RPC connection error")
        if call_count == 2:
            # Second call succeeds — proves keeper kept running
            stop_event.set()
            results_from_successful_call.append({"status": "executed", "order_key": "0xabc"})
            return results_from_successful_call
        return []

    with (
        caplog.at_level(logging.WARNING, logger="orchestrator.loop.keeper_monitor"),
        patch(
            "orchestrator.loop.keeper_monitor.execute_ready_orders",
            side_effect=fake_execute_ready_orders,
        ),
    ):
        await run_keeper_monitor(
            web3=AsyncMock(),
            mock_perps=AsyncMock(),
            db_session=AsyncMock(),
            deployer_address="0xDeployer",
            vault_address="0xVault0000000000000000000000000000000000",
            redis=None,
            session_id="00000000-0000-0000-0000-000000000001",
            stop_event=stop_event,
            poll_seconds=0.01,
        )

    # Must have been called at least twice (first raised, second ran, then stopped)
    assert call_count >= 2, (
        f"Expected keeper to poll at least twice, got {call_count}. "
        "WR-05 regression: keeper stopped after the first exception."
    )

    # A warning log must have been emitted for the transient failure
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "unhandled exception" in msg.lower() or "will retry" in msg.lower() for msg in warning_msgs
    ), (
        f"Expected a WARNING log about the exception; got: {warning_msgs}. "
        "WR-05: keeper must log transient failures."
    )

    # The successful second call should have run
    assert len(results_from_successful_call) == 1, (
        "Expected the second (successful) execute_ready_orders call to have run. "
        "WR-05 regression: keeper stopped before processing the recovery call."
    )


@pytest.mark.asyncio
async def test_keeper_cancelled_error_propagates() -> None:
    """CancelledError must propagate cleanly — cancellation is not swallowed.

    WR-05 requirement: the outer try/except must NOT catch asyncio.CancelledError.
    """
    from unittest.mock import AsyncMock, patch

    from orchestrator.loop.keeper_monitor import run_keeper_monitor

    stop_event = asyncio.Event()

    async def fake_execute_raises_cancelled(*args, **kwargs):
        raise asyncio.CancelledError("task cancelled")

    with patch(
        "orchestrator.loop.keeper_monitor.execute_ready_orders",
        side_effect=fake_execute_raises_cancelled,
    ):
        # The keeper task should propagate CancelledError out of run_keeper_monitor
        with pytest.raises(asyncio.CancelledError):
            await run_keeper_monitor(
                web3=AsyncMock(),
                mock_perps=AsyncMock(),
                db_session=AsyncMock(),
                deployer_address="0xDeployer",
                vault_address="0xVault0000000000000000000000000000000000",
                redis=None,
                session_id="00000000-0000-0000-0000-000000000001",
                stop_event=stop_event,
                poll_seconds=0.01,
            )

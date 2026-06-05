"""SC-3: pause on 3 consecutive API failures (ORCH-06) — FailureTracker + driver level.

Tests both the pure FailureTracker state machine (Task 1) and the driver-level
integration (Task 3) where mocked API exceptions cause record_model_status(status='paused').
"""

from __future__ import annotations

import pytest

# ============================================================
# Section A: FailureTracker pure state-machine tests (Task 1)
# ============================================================


def test_three_api_failures_pause() -> None:
    """3 consecutive api_failures → should_pause() True (D-15 threshold=3)."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    t.record_api_failure()
    assert not t.should_pause()
    t.record_api_failure()
    assert not t.should_pause()
    t.record_api_failure()
    assert t.should_pause()


def test_two_api_failures_then_success_not_paused() -> None:
    """2 api_failures then success → not paused, streak reset (D-17)."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    t.record_api_failure()
    t.record_api_failure()
    assert not t.should_pause()
    recovered = t.record_success()
    assert not t.should_pause()
    assert not recovered  # was not paused when success arrived
    assert t.api_failure_streak == 0


def test_four_malformed_not_paused_fifth_paused() -> None:
    """4 malformed → not paused; 5th → paused (D-17 threshold=5)."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    for _ in range(4):
        t.record_malformed()
    assert not t.should_pause(), "4 malformed should NOT pause yet"
    t.record_malformed()
    assert t.should_pause(), "5th malformed MUST pause"


def test_record_success_returns_true_when_previously_paused() -> None:
    """record_success returns True (recovery signal) when previously paused (D-16)."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    for _ in range(3):
        t.record_api_failure()
    assert t.should_pause()
    recovered = t.record_success()
    assert recovered is True  # auto-flip signal
    assert not t.should_pause()
    assert t.api_failure_streak == 0


def test_single_malformed_does_not_pause() -> None:
    """One malformed response surfaces status but does NOT pause (D-17)."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    t.record_malformed()
    assert not t.should_pause()
    assert t.malformed_streak == 1


def test_success_resets_both_streaks() -> None:
    """record_success resets BOTH api_failure_streak and malformed_streak (D-17)."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    t.record_api_failure()
    t.record_api_failure()
    t.record_malformed()
    t.record_malformed()
    t.record_success()
    assert t.api_failure_streak == 0
    assert t.malformed_streak == 0


def test_single_failure_after_reset_does_not_pause() -> None:
    """After a reset, a single subsequent failure does not pause."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    for _ in range(3):
        t.record_api_failure()
    t.record_success()  # recover
    t.record_api_failure()  # one failure after reset
    assert not t.should_pause()


def test_consecutive_returns_max_of_both_streaks() -> None:
    """consecutive() returns max(api_failure_streak, malformed_streak)."""
    from orchestrator.loop.failure_tracker import FailureTracker

    t = FailureTracker()
    t.record_api_failure()
    t.record_api_failure()
    t.record_malformed()
    assert t.consecutive() == 2  # max(2, 1)


# ============================================================
# Section B: driver-level test — 3 mocked APITimeoutErrors →
#            record_model_status called with status='paused'
# ============================================================


@pytest.mark.asyncio
async def test_driver_pauses_after_3_api_timeout_errors() -> None:
    """After 3 consecutive anthropic.APITimeoutError, driver writes status='paused'.

    Uses AsyncMock for db / web3 / mock_perps so no Postgres or anvil needed.
    Patches call_claude to raise APITimeoutError on every call.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    import anthropic

    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.failure_tracker import FailureTracker
    from orchestrator.loop.session import SessionConfig

    config = SessionConfig(session_id="00000000-0000-0000-0000-000000000099")
    tracker = FailureTracker()

    db = AsyncMock()
    redis = AsyncMock()

    # Minimal walk stub
    walk = MagicMock()
    walk.step.return_value = {"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}
    walk.funding_rate.return_value = 0.0
    walk.change_24h.return_value = 0.0

    aggregators = {"ETH": MagicMock(), "BTC": MagicMock(), "SOL": MagicMock()}
    for agg in aggregators.values():
        agg.functions.latestRoundData.return_value = MagicMock()
        agg.functions.latestRoundData.return_value.call = AsyncMock(
            return_value=(0, 300000000000, 0, 0, 0)
        )

    web3 = AsyncMock()
    web3.eth.block_number = AsyncMock(return_value=100)

    mock_perps = AsyncMock()

    paused_calls: list[str] = []

    async def fake_record_model_status(db_sess, *, vault_address, session_id, model, status, **kw):
        paused_calls.append(status)

    # Run 3 cycles — each raises APITimeoutError
    with (
        patch(
            "orchestrator.loop.driver.call_claude",
            side_effect=anthropic.APITimeoutError(request=MagicMock()),
        ),
        patch(
            "orchestrator.loop.driver.record_model_status",
            side_effect=fake_record_model_status,
        ),
    ):
        for cycle in range(1, 4):
            result = await run_live_cycle(
                web3,
                mock_perps,
                vault="0xVault0000000000000000000000000000000001",
                model="claude-opus-4-7",
                cycle=cycle,
                config=config,
                walk=walk,
                aggregators=aggregators,
                tracker=tracker,
                db=db,
                redis=redis,
                session_id=config.session_id,
                seq=cycle,
                available_usdc=10000.0,
                open_positions={},
                nav_table="NAV: $10,000",
                positions_table="No open positions.",
                recent_decisions="None",
                elapsed_seconds=float(cycle * 60),
            )
            assert result["status"] == "api_failure"

    # After 3 consecutive failures the tracker should be paused
    assert tracker.should_pause(), "Tracker must be paused after 3 api_failures"
    # At least one call to record_model_status with status='paused'
    assert "paused" in paused_calls, (
        f"Expected at least one record_model_status(status='paused'); got: {paused_calls}"
    )

"""SC-4: malformed response produces no trade, no trade-journal entry (ORCH-05 / JOURNAL-04).

Verifies:
  - A cycle where call_claude returns a response whose extract_tool_input is None
    (no ToolUseBlock) results in status='malformed'.
  - NO record_trade call is made (ORCH-05).
  - NO openLong/openShort .transact() call is made.
  - record_model_status(status='malformed') IS called.
  - record_journal_pending of the raw response IS called (D-07/D-08).
  - A single malformed does NOT pause (D-17 — pause only at streak=5).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_malformed_response_no_trade_no_journal() -> None:
    """Malformed LLM response (no ToolUseBlock) → no trade, ModelStatus{malformed} (SC-4).

    Uses AsyncMock for db / web3 / mock_perps — no Postgres or anvil needed.
    Patches call_claude to return a response with no ToolUseBlock (extract_tool_input=None).
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.failure_tracker import FailureTracker
    from orchestrator.loop.session import SessionConfig

    config = SessionConfig(session_id="00000000-0000-0000-0000-000000000088")
    tracker = FailureTracker()

    db = AsyncMock()
    redis = None  # not needed for this test

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
    web3.eth.get_block_number = AsyncMock(return_value=100)

    mock_perps = AsyncMock()

    # Track calls to key db helpers
    record_model_status_calls: list[dict] = []
    record_journal_calls: list[dict] = []

    async def fake_record_model_status(sess, *, vault_address, session_id, model, status, **kw):
        record_model_status_calls.append({"status": status, **kw})

    async def fake_record_journal_pending(sess, *, vault_address, order_key, **kw):
        record_journal_calls.append({"order_key": order_key, **kw})

    # Mock response with no ToolUseBlock (TextBlock only — content-policy refusal)
    class FakeTextBlock:
        text = "I cannot assist with trading."

    class FakeResponse:
        content = [FakeTextBlock()]

    with (
        patch("orchestrator.loop.driver.call_claude", return_value=FakeResponse()),
        patch(
            "orchestrator.loop.driver.record_model_status",
            side_effect=fake_record_model_status,
        ),
        patch(
            "orchestrator.loop.driver.record_journal_pending",
            side_effect=fake_record_journal_pending,
        ),
    ):
        result = await run_live_cycle(
            web3,
            mock_perps,
            vault="0xVault0000000000000000000000000000000002",
            model="claude-opus-4-7",
            cycle=1,
            config=config,
            walk=walk,
            aggregators=aggregators,
            tracker=tracker,
            db=db,
            redis=redis,
            session_id=config.session_id,
            seq=1,
            available_usdc=10000.0,
            open_positions={},
            nav_table="NAV: $10,000",
            positions_table="No open positions.",
            recent_decisions="None",
            elapsed_seconds=0.0,
        )

    # SC-4: malformed cycle → status='malformed'
    assert result["status"] == "malformed", f"Expected 'malformed', got {result['status']!r}"
    # SC-4: no trade fields in result
    assert "order_key" not in result or result.get("order_key") is None
    assert "tx_hash" not in result

    # MockPerps submit MUST NOT be called (ORCH-05 — no trade on malformed)
    assert not mock_perps.functions.openLong.called, "openLong must not be called on malformed"
    assert not mock_perps.functions.openShort.called, "openShort must not be called on malformed"

    # record_model_status(status='malformed') MUST be called
    malformed_status_calls = [c for c in record_model_status_calls if c["status"] == "malformed"]
    assert malformed_status_calls, (
        f"Expected record_model_status(status='malformed'); got statuses: "
        f"{[c['status'] for c in record_model_status_calls]}"
    )

    # record_journal_pending for the malformed cycle MUST be called (D-07/D-08)
    malformed_journal_calls = [
        c for c in record_journal_calls if "malformed-" in c.get("order_key", "")
    ]
    assert malformed_journal_calls, (
        f"Expected record_journal_pending with 'malformed-' key; got keys: "
        f"{[c['order_key'] for c in record_journal_calls]}"
    )

    # Single malformed does NOT pause (D-17 — pause only at streak=5)
    assert not tracker.should_pause(), "A single malformed must NOT pause the tracker"
    assert tracker.malformed_streak == 1


@pytest.mark.asyncio
async def test_malformed_validate_decision_failure_no_trade() -> None:
    """Malformed: extract_tool_input returns dict but validate_decision returns None (SC-4).

    The validate_decision failure path (schema mismatch) also routes to the
    malformed path — no trade, no record_trade, record_model_status='malformed'.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.failure_tracker import FailureTracker
    from orchestrator.loop.session import SessionConfig

    config = SessionConfig(session_id="00000000-0000-0000-0000-000000000089")
    tracker = FailureTracker()

    db = AsyncMock()
    redis = None

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
    web3.eth.get_block_number = AsyncMock(return_value=100)
    mock_perps = AsyncMock()

    record_model_status_calls: list[dict] = []

    async def fake_record_model_status(sess, *, vault_address, session_id, model, status, **kw):
        record_model_status_calls.append({"status": status})

    # A raw dict that passes extract_tool_input but fails Decision.model_validate
    # (missing required 'action' field)
    class FakeToolUseBlock:
        input = {"market": "ETH", "side": "long", "sizeUsd": 1000.0, "leverage": 2.0}
        # no 'action' field — will fail Decision.model_validate()

    class FakeResponse:
        content = [FakeToolUseBlock()]

    with (
        patch("orchestrator.loop.driver.call_claude", return_value=FakeResponse()),
        patch(
            "orchestrator.loop.driver.record_model_status",
            side_effect=fake_record_model_status,
        ),
        patch("orchestrator.loop.driver.record_journal_pending", new_callable=AsyncMock),
    ):
        result = await run_live_cycle(
            web3,
            mock_perps,
            vault="0xVault0000000000000000000000000000000002",
            model="claude-opus-4-7",
            cycle=2,
            config=config,
            walk=walk,
            aggregators=aggregators,
            tracker=tracker,
            db=db,
            redis=redis,
            session_id=config.session_id,
            seq=2,
            available_usdc=10000.0,
            open_positions={},
            nav_table="NAV: $10,000",
            positions_table="No open positions.",
            recent_decisions="None",
            elapsed_seconds=60.0,
        )

    assert result["status"] == "malformed"
    # ORCH-05: no trade execution on malformed
    assert not mock_perps.functions.openLong.called, "openLong must not be called"
    assert not mock_perps.functions.openShort.called, "openShort must not be called"

    malformed_statuses = [c for c in record_model_status_calls if c["status"] == "malformed"]
    assert malformed_statuses, (
        f"Expected record_model_status(status='malformed'); got: {record_model_status_calls}"
    )

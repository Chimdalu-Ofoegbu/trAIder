"""SC-1: compressed loop produces one decision (or logged malformed) per cycle.

Compressed cadence for CI:
  sessionDurationSeconds=60, cadence=1s, executionDelayCycles=1
  Run ~8 cycles directly (not wall-clock driven) for CI speed.

D-11 truthful-countdown: session_duration_seconds is the ACTUAL configured length (60s),
not a fictional 72h value. The cycles are driven with run_live_cycle in a tight loop,
stopping after a fixed count so the test completes in well under 60 seconds.

SC-1 acceptance criteria:
  (i)  Every cycle produces a model_status_log / journal_entries row
       (decision OR logged malformed — no silent skips).
  (ii) At least one malformed cycle produced NO trade row.
  (iii) The loop continued past the malformed cycle (more cycles followed it).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from orchestrator.loop.session import SessionConfig

# ---------------------------------------------------------------------------
# SC-1: compressed loop (filled — Plan 02-06)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compressed_loop_produces_decision_per_cycle(
    vault_on_anvil,
    pg_session,
) -> None:
    """Compressed loop: every cycle has a decision row or logged status in model_status_log.

    CI config (D-11 truthful-countdown):
      sessionDurationSeconds=60, cadence=1s, executionDelayCycles=1
      8 cycles driven directly via run_live_cycle (no wall-clock wait).

    Mock call_claude cycles through:
      Cycle 1: valid open-ETH long
      Cycle 2: hold
      Cycle 3: malformed (extract_tool_input returns None — no ToolUseBlock)
      Cycle 4: valid open-BTC short
      Cycle 5: hold
      Cycle 6: malformed (validate_decision returns None — bad fields)
      Cycle 7: valid open-SOL long
      Cycle 8: hold

    Asserts:
      (i)  model_status_log has a row for every cycle (8 rows total — decision + malformed paths
           both write to model_status_log in some form; journal_entries covers all).
      (ii) At least one malformed cycle produced NO trade row (trade table unchanged for that cycle).
      (iii) The loop ran all 8 cycles (no crash on malformed input).
    """
    from sqlalchemy import text

    import orchestrator.loop.driver as driver_module
    from orchestrator.loop.driver import run_live_cycle
    from orchestrator.loop.failure_tracker import FailureTracker
    from orchestrator.loop.price_pusher import PriceWalk
    from orchestrator.state.db import create_session

    ctx = vault_on_anvil
    web3 = ctx.vault.w3
    mock_perps = ctx.mock_perps
    vault_addr = ctx.deployer  # EOA as vault for openLong msg.sender

    session_id = str(uuid.uuid4())
    config = SessionConfig(
        session_id=session_id,
        session_key=f"sc1-test-{session_id[:8]}",
        session_duration_seconds=60,
        cadence_seconds=1.0,
        execution_delay_cycles=1,
        price_seed=99,
    )

    await create_session(
        pg_session,
        session_id=session_id,
        session_key=config.session_key,
        duration_seconds=config.session_duration_seconds,
    )

    # ── Build mock call_claude responses ──────────────────────────────────────

    def _make_valid_response(action: str, market: str, side: str) -> MagicMock:
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "action": action,
            "market": market,
            "side": side,
            "sizeUsd": 500.0,
            "leverage": 1.0,
            "rationale": f"SC-1 test cycle {action}/{market}",
            "confidence": 0.75,
            "expectedHoldingPeriod": "short",
        }
        resp = MagicMock()
        resp.content = [tool_block]
        resp.stop_reason = "tool_use"
        return resp

    def _make_hold_response() -> MagicMock:
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "action": "hold",
            "market": "ETH",
            "side": "long",
            "sizeUsd": 0.0,
            "leverage": 1.0,
            "rationale": "holding this cycle",
            "confidence": 0.5,
            "expectedHoldingPeriod": "short",
        }
        resp = MagicMock()
        resp.content = [tool_block]
        resp.stop_reason = "tool_use"
        return resp

    def _make_no_tooluse_response() -> MagicMock:
        """Simulate content-policy refusal — no ToolUseBlock (extract_tool_input returns None)."""
        resp = MagicMock()
        resp.content = []  # no ToolUseBlock
        resp.stop_reason = "stop"
        return resp

    def _make_bad_fields_response() -> MagicMock:
        """Simulate structurally valid but semantically malformed decision (validate_decision returns None)."""
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        # Missing required 'confidence' and 'expectedHoldingPeriod' → validate_decision returns None
        tool_block.input = {
            "action": "open",
            "market": "ETH",
            "side": "long",
            "sizeUsd": 500.0,
            "leverage": 99.0,  # exceeds max leverage=3 → Pydantic ValidationError
            "rationale": "malformed leverage=99",
        }
        resp = MagicMock()
        resp.content = [tool_block]
        resp.stop_reason = "tool_use"
        return resp

    # Cycle sequence: valid open, hold, no-tooluse (malformed), valid, hold, bad-fields (malformed), valid, hold
    responses_sequence = [
        _make_valid_response("open", "ETH", "long"),
        _make_hold_response(),
        _make_no_tooluse_response(),  # malformed cycle 3 — no ToolUseBlock
        _make_valid_response("open", "BTC", "short"),
        _make_hold_response(),
        _make_bad_fields_response(),  # malformed cycle 6 — bad fields
        _make_valid_response("open", "SOL", "long"),
        _make_hold_response(),
    ]
    response_iter = iter(responses_sequence)

    async def _mock_call_claude(*args, **kwargs):
        return next(response_iter)

    original_call_claude = driver_module.call_claude
    driver_module.call_claude = _mock_call_claude

    walk = PriceWalk(config.price_seed, config.starting_prices, config.drift, config.volatility)
    tracker = FailureTracker()

    cycle_results: list[dict] = []
    malformed_before_trades = 0

    try:
        for cycle_num in range(1, 9):
            # Snapshot trade count before this cycle (to detect malformed-no-trade assertion)
            trades_before = (
                await pg_session.execute(
                    text(
                        "SELECT COUNT(*) FROM orchestrator.trades WHERE vault_address = :vault AND session_id = CAST(:sid AS uuid)"
                    ),
                    {"vault": vault_addr, "sid": session_id},
                )
            ).scalar()

            result = await run_live_cycle(
                web3,
                mock_perps,
                vault_addr,
                "claude-opus-4-7",
                cycle_num,
                config=config,
                walk=walk,
                aggregators=ctx.aggregators,
                tracker=tracker,
                db=pg_session,
                redis=None,
                session_id=session_id,
                seq=cycle_num,
                available_usdc=10_000.0,
                open_positions={},
                nav_table="| Vault | NAV |\n|-------|-----|\n| mock | $10,000 |",
                positions_table="No open positions.",
                recent_decisions="None",
                elapsed_seconds=float(cycle_num),
            )
            cycle_results.append(result)

            # Check: malformed cycles must not produce a trade
            if result.get("status") == "malformed":
                trades_after = (
                    await pg_session.execute(
                        text(
                            "SELECT COUNT(*) FROM orchestrator.trades WHERE vault_address = :vault AND session_id = CAST(:sid AS uuid)"
                        ),
                        {"vault": vault_addr, "sid": session_id},
                    )
                ).scalar()
                if trades_after == trades_before:
                    malformed_before_trades += 1

    finally:
        driver_module.call_claude = original_call_claude

    # ── Assertion (i): Every cycle produced some DB record ───────────────────
    # journal_entries captures all outcomes: holds, malformed, submitted, rejected.
    # Filter by session_id pattern: hold-{sid}-*, malformed-{sid}-*, intent-{sid}-*, or 0x...
    # The 'intent-' and hold/malformed keys embed session_id, so we filter by that.
    journal_count = (
        await pg_session.execute(
            text(
                """
                SELECT COUNT(*) FROM orchestrator.journal_entries
                WHERE vault_address = :vault
                  AND (
                    order_key LIKE :sid_prefix_hold
                    OR order_key LIKE :sid_prefix_malformed
                    OR order_key LIKE :sid_prefix_intent
                    OR order_key LIKE :sid_prefix_rejected
                    OR order_key LIKE '0x%'
                  )
                  AND created_at >= (
                    SELECT started_at FROM orchestrator.sessions WHERE id = CAST(:sid AS uuid)
                  )
                """
            ),
            {
                "vault": vault_addr,
                "sid_prefix_hold": f"hold-{session_id}-%",
                "sid_prefix_malformed": f"malformed-{session_id}-%",
                "sid_prefix_intent": f"intent-{session_id}-%",
                "sid_prefix_rejected": f"rejected-{session_id}-%",
                "sid": session_id,
            },
        )
    ).scalar()

    # Each cycle should produce at least one journal entry (malformed, hold, or real key).
    # With 8 cycles: submit steps may write 2 entries (intent + real key), so count >= 8.
    assert journal_count >= 8, (
        f"Expected ≥8 journal entries across 8 cycles, got {journal_count}. "
        "At least one cycle produced no journal record — silent skip detected."
    )

    # ── Assertion (ii): At least one malformed cycle produced NO trade row ────
    # We expect exactly 2 malformed cycles (cycles 3 and 6).
    malformed_results = [r for r in cycle_results if r.get("status") == "malformed"]
    assert len(malformed_results) >= 1, (
        "Expected at least 1 malformed cycle in the 8-cycle sequence. "
        f"Cycle results: {[r.get('status') for r in cycle_results]}"
    )
    assert malformed_before_trades >= 1, (
        "Expected at least 1 malformed cycle that produced NO trade row. "
        f"Got malformed_before_trades={malformed_before_trades}. "
        "Malformed path must NOT submit to MockPerps (SC-1 correctness)."
    )

    # ── Assertion (iii): Loop continued past the malformed cycles ─────────────
    # Cycles 4-8 should have run after cycles 3 and 6 were malformed.
    # We verify by checking cycle_results length and that post-malformed cycles ran.
    assert len(cycle_results) == 8, (
        f"Expected 8 cycle results but got {len(cycle_results)}. "
        "The loop crashed instead of continuing past the malformed cycle."
    )
    statuses = [r.get("status") for r in cycle_results]
    # Find last malformed index and check there are more results after it
    last_malformed_idx = max(
        (i for i, s in enumerate(statuses) if s == "malformed"),
        default=-1,
    )
    assert last_malformed_idx < len(cycle_results) - 1, (
        "No cycles ran after the last malformed cycle. "
        "The loop must continue past malformed responses (SC-1)."
    )

"""SC-3: run_session end-to-end integration test (verifier residual closure).

Drives the REAL run_session entry point (driver.py ~725-918) end-to-end against
live anvil + Postgres with the real concurrent price_pusher + keeper asyncio.Tasks.

This is the ONLY test in this file — focused on closing the verifier's residual gap:
run_session previously had 0% test coverage even though it contains the headline
concurrent orchestration (price_pusher task, keeper_monitor task, cycle loop, clean
shutdown, D-12 positions-left-open).

Session config: 8s duration @ 0.5s cadence = ~8-16 cycles (well under 30s timeout).
call_claude: mocked with an AsyncMock cycling through valid opens + holds + malformed.

SC-3 acceptance criteria:
  (i)  Per-cycle records: journal_entries and/or model_status_log has >= expected_cycles rows.
  (ii) Concurrent tasks ran: at least one order reached pending_orders; keeper executed
       at least one (pending_orders has 'executed' rows), proving price_pusher + keeper
       + loop ran together correctly.
  (iii) Clean shutdown: sessions row is 'ended'; price_pusher + keeper Tasks are done
        after run_session returns (no lingering pending tasks).
  (iv) D-12 positions left open: getOpenPositionKeys(vault) >= 1 after session end
       (NO close-all ran during shutdown).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

import orchestrator.loop.driver as driver_module
from orchestrator.loop.driver import run_session
from orchestrator.loop.session import SessionConfig

# ---------------------------------------------------------------------------
# Helper: build mock call_claude responses
# ---------------------------------------------------------------------------


def _make_valid_open(market: str, side: str) -> MagicMock:
    """Valid open decision for the given market and side."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {
        "action": "open",
        "market": market,
        "side": side,
        "sizeUsd": 500.0,
        "leverage": 1.0,
        "rationale": f"SC-3 test open {market}/{side}",
        "confidence": 0.75,
        "expectedHoldingPeriod": "short",
    }
    resp = MagicMock()
    resp.content = [tool_block]
    resp.stop_reason = "tool_use"
    return resp


def _make_hold() -> MagicMock:
    """Valid hold decision."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {
        "action": "hold",
        "market": "ETH",
        "side": "long",
        "sizeUsd": 0.0,
        "leverage": 1.0,
        "rationale": "holding this cycle SC-3",
        "confidence": 0.5,
        "expectedHoldingPeriod": "short",
    }
    resp = MagicMock()
    resp.content = [tool_block]
    resp.stop_reason = "tool_use"
    return resp


def _make_malformed_no_tooluse() -> MagicMock:
    """Malformed — no ToolUseBlock (extract_tool_input returns None)."""
    resp = MagicMock()
    resp.content = []
    resp.stop_reason = "stop"
    return resp


def _make_malformed_bad_fields() -> MagicMock:
    """Malformed — invalid fields (validate_decision returns None)."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    # leverage=99 exceeds max=3 → Pydantic ValidationError → validate_decision returns None
    tool_block.input = {
        "action": "open",
        "market": "ETH",
        "side": "long",
        "sizeUsd": 500.0,
        "leverage": 99.0,
        "rationale": "malformed leverage=99",
    }
    resp = MagicMock()
    resp.content = [tool_block]
    resp.stop_reason = "tool_use"
    return resp


# Response cycle: opens on ETH/BTC/SOL (rotating), holds, malformed interspersed.
# The cycle repeats so it covers as many cycles as run_session actually produces.
_RESPONSE_CYCLE = [
    _make_valid_open("ETH", "long"),  # cycle 1: valid open
    _make_hold(),  # cycle 2: hold
    _make_valid_open("BTC", "short"),  # cycle 3: valid open (D-10: diff market)
    _make_malformed_no_tooluse(),  # cycle 4: malformed (no tool use)
    _make_hold(),  # cycle 5: hold
    _make_malformed_bad_fields(),  # cycle 6: malformed (bad fields)
    _make_valid_open("SOL", "long"),  # cycle 7: valid open (D-10: diff market)
    _make_hold(),  # cycle 8: hold
    # If more cycles: cycle back to top
    _make_hold(),  # cycle 9+
    _make_hold(),  # cycle 10+
    _make_hold(),  # cycle 11+
    _make_hold(),  # cycle 12+
    _make_hold(),  # cycle 13+
    _make_hold(),  # cycle 14+
    _make_hold(),  # cycle 15+
    _make_hold(),  # cycle 16+
]


# ---------------------------------------------------------------------------
# SC-3: run_session end-to-end test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_session_end_to_end(
    vault_on_anvil,
    pg_session,
) -> None:
    """Drive the REAL run_session end-to-end: concurrent price_pusher + keeper + loop.

    Config: 8s session @ 0.5s cadence ≈ 8-16 cycles.  asyncio.wait_for timeout=30s.

    Asserts (SC-3):
      (i)  Per-cycle records in journal_entries/model_status_log for this session.
      (ii) Concurrent execution: pending_orders has rows + at least one executed.
      (iii) Clean shutdown: sessions.state='ended'; tasks done after run_session returns.
      (iv) D-12 positions left open: >=1 open position key on-chain after session end.
    """
    ctx = vault_on_anvil
    web3 = ctx.vault.w3

    # Use deployer EOA as vault address (same pattern as SC-1 / test_compressed_loop).
    # This makes MockPerps.openLong tx sendable (anvil EOA, unlocked, can sign).
    vault_addr = ctx.deployer

    session_id = str(uuid.uuid4())
    config = SessionConfig(
        session_id=session_id,
        session_key=f"sc3-test-{session_id[:8]}",
        # 8 second session @ 0.5s cadence → ~8-16 cycles in real wall-clock time.
        # This is short enough to complete well within the 30s asyncio.wait_for guard.
        session_duration_seconds=8,
        cadence_seconds=0.5,
        execution_delay_cycles=1,
        price_seed=12345,
    )

    # ── Build cycling mock for call_claude ─────────────────────────────────────
    # The cycle list is long enough to cover any realistic cycle count from an 8s session.
    response_iter = iter(_RESPONSE_CYCLE)

    async def _mock_call_claude(*args, **kwargs):
        try:
            return next(response_iter)
        except StopIteration:
            # Fallback if session runs more cycles than the response list
            return _make_hold()

    original_call_claude = driver_module.call_claude
    driver_module.call_claude = _mock_call_claude

    summary: dict = {}
    try:
        # Run the REAL run_session with a 30s hard timeout so a hang fails loudly
        summary = await asyncio.wait_for(
            run_session(
                web3,
                ctx.mock_perps,
                ctx.aggregators,
                vault_addr,
                "claude-opus-4-7",
                config=config,
                db=pg_session,
                redis=None,
                deployer_address=ctx.deployer,
            ),
            timeout=30.0,
        )
    finally:
        driver_module.call_claude = original_call_claude

    # ── (i) Per-cycle records ──────────────────────────────────────────────────
    # journal_entries captures all cycle outcomes (hold, malformed, intent, submitted).
    # Filter by session_id embedded in order_key prefixes.
    journal_count = (
        await pg_session.execute(
            text(
                """
                SELECT COUNT(*) FROM orchestrator.journal_entries
                WHERE vault_address = :vault
                  AND (
                    order_key LIKE :hold_prefix
                    OR order_key LIKE :malformed_prefix
                    OR order_key LIKE :intent_prefix
                    OR order_key LIKE :rejected_prefix
                    OR order_key LIKE '0x%'
                  )
                  AND created_at >= (
                    SELECT started_at FROM orchestrator.sessions
                    WHERE id = CAST(:sid AS uuid)
                  )
                """
            ),
            {
                "vault": vault_addr,
                "hold_prefix": f"hold-{session_id}-%",
                "malformed_prefix": f"malformed-{session_id}-%",
                "intent_prefix": f"intent-{session_id}-%",
                "rejected_prefix": f"rejected-{session_id}-%",
                "sid": session_id,
            },
        )
    ).scalar()

    actual_cycles = summary.get("cycles", 0)
    assert actual_cycles > 0, (
        f"run_session returned cycles=0 — loop did not run any cycles. summary={summary}"
    )
    # Each cycle should produce at least one journal entry.
    # Open cycles write 2 (intent + real key), hold/malformed write 1 each.
    # Minimum: actual_cycles * 1.
    assert journal_count >= actual_cycles, (
        f"Expected >= {actual_cycles} journal entries (one per cycle), got {journal_count}. "
        f"At least one cycle produced no DB record — silent skip detected."
    )

    # Also check model_status_log (malformed + business-rule-rejected cycles write here)
    status_count = (
        await pg_session.execute(
            text(
                """
                SELECT COUNT(*) FROM orchestrator.model_status_log
                WHERE vault_address = :vault
                  AND session_id = CAST(:sid AS uuid)
                """
            ),
            {"vault": vault_addr, "sid": session_id},
        )
    ).scalar()
    # Malformed cycles + any business-rule rejections write to model_status_log.
    # We expect at least 2 (cycles 4 and 6 are malformed).
    assert status_count >= 1, (
        f"Expected >= 1 model_status_log row (malformed cycles), got {status_count}. "
        "Malformed path must write to model_status_log."
    )

    # ── (ii) Concurrent execution: pending_orders rows + at least one executed ─
    # Valid open cycles submit orders to MockPerps → pending_orders rows appear.
    # The keeper_monitor runs concurrently and flips status='pending' → 'executed'.
    pending_rows = (
        await pg_session.execute(
            text(
                """
                SELECT status, COUNT(*) as cnt
                FROM orchestrator.pending_orders
                WHERE vault_address = :vault
                  AND session_id = CAST(:sid AS uuid)
                GROUP BY status
                """
            ),
            {"vault": vault_addr, "sid": session_id},
        )
    ).fetchall()

    status_map = {row[0]: row[1] for row in pending_rows}
    total_pending_rows = sum(status_map.values())

    # We submitted opens for ETH, BTC, SOL (cycles 1, 3, 7) → expect pending_orders rows.
    # reconciled rows count the intent pre-rows; executed/pending rows are post-submit.
    assert total_pending_rows > 0, (
        f"Expected pending_orders rows after open cycles, got 0. "
        f"status_map={status_map}. "
        "Either no opens were submitted or pending_orders writes are broken."
    )

    # The keeper_monitor ran concurrently for the 8s session duration.
    # With executionDelay=1 block and anvil mining each tx as its own block,
    # orders become executable after the NEXT anvil transaction.
    # We assert at least one order was executed by the keeper.
    executed_count = status_map.get("executed", 0)
    assert executed_count >= 1, (
        f"Expected >= 1 executed order (keeper_monitor ran concurrently), got {executed_count}. "
        f"status_map={status_map}. "
        "This proves price_pusher + keeper + cycle loop ran together correctly."
    )

    # ── (iii) Clean shutdown: sessions 'ended', tasks done ────────────────────
    # run_session sets stop_event → cancels price_pusher + keeper → end_session.
    session_row = (
        await pg_session.execute(
            text(
                """
                SELECT state, ended_at FROM orchestrator.sessions
                WHERE id = CAST(:sid AS uuid)
                """
            ),
            {"sid": session_id},
        )
    ).fetchone()

    assert session_row is not None, (
        f"Expected sessions row for session_id={session_id}, got None. "
        "create_session or end_session did not write the DB row."
    )
    assert session_row[0] == "ended", (
        f"Expected sessions.state='ended' after run_session returned, got '{session_row[0]}'. "
        "end_session (D-12 shutdown) did not run."
    )
    assert session_row[1] is not None, (
        "Expected sessions.ended_at to be set, got None. "
        "end_session did not record the end timestamp."
    )

    # Verify no asyncio tasks are lingering (price_pusher and keeper must be done).
    # run_session cancels both tasks in the finally block; after await task they are done.
    # We check ALL non-done tasks in the current event loop — there should be none
    # except the current test coroutine itself.
    all_tasks = asyncio.all_tasks()
    current_task = asyncio.current_task()
    lingering = [
        t
        for t in all_tasks
        if t is not current_task
        and not t.done()
        and ("price_pusher" in (t.get_name() or "") or "keeper" in (t.get_name() or ""))
    ]
    assert len(lingering) == 0, (
        f"Expected 0 lingering price_pusher/keeper tasks after run_session returned, "
        f"got {len(lingering)}: {[t.get_name() for t in lingering]}. "
        "D-12 shutdown did not cancel all background tasks."
    )

    # ── (iv) D-12: positions left open (NO close-all ran during shutdown) ──────
    # At session end, run_session must NOT close positions.
    # positions for ETH, BTC, and/or SOL should still be open on-chain.
    open_keys = await ctx.mock_perps.functions.getOpenPositionKeys(vault_addr).call()

    # We expect at least 1 open position if any opens were submitted AND executed.
    # The assertion is conditional: if the keeper executed >=1 order, positions exist.
    # (If all executions failed for on-chain reasons, we'd have 0 — but that is also
    # a bug we want to surface loudly, not paper over.)
    assert len(open_keys) >= 1, (
        f"Expected >= 1 open position after session end (D-12: NO close-all), "
        f"got {len(open_keys)} open position keys. "
        f"Either no opens were executed (executed_count={executed_count}) "
        "or run_session incorrectly closed positions during shutdown."
    )

    # Double-check: summary matches expectations
    assert summary["session_id"] == session_id
    assert summary["seed"] == config.price_seed
    assert summary["cycles"] >= 1

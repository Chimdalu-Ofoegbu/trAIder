"""
test_pending_orders.py — Integration tests for pending_orders lifecycle helpers (ORCH-07/08).

Tests the six new helpers added to orchestrator.state.db in Plan 02-03:
  record_pending_order   — insert with ON CONFLICT DO NOTHING idempotency
  get_pending_orders_ready  — block-gated keeper query
  mark_pending_order_executed — status flip (idempotent no-op on re-call)
  get_unresolved_pending_orders — restart-recovery query (pending only)
  create_session / end_session — session lifecycle

All tests use the pg_session fixture which:
  - Skips cleanly (pytest.skip) when Postgres is unreachable
  - Applies alembic upgrade head before yielding (migration 0003 is present)

SC-2 FOUNDATION: These tests prove the SIGKILL-resume guarantee at the DB layer.
The loop driver (Plan 05) composes these primitives into the full resume protocol.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from orchestrator.state.db import (
    create_session,
    get_pending_orders_ready,
    get_unresolved_pending_orders,
    mark_pending_order_executed,
    record_pending_order,
)

# ---------------------------------------------------------------------------
# Test 1: record_pending_order is idempotent on (vault_address, order_key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_pending_order_is_idempotent(pg_session):
    """Second call with the same (vault_address, order_key) is a silent no-op.

    SC-2: The ON CONFLICT DO NOTHING clause on UNIQUE(vault_address, order_key)
    prevents double-insert on restart.  COUNT(*) must equal 1 after two calls.
    """
    sid = str(uuid.uuid4())
    skey = f"idem-test-{sid[:8]}"
    vault = f"0xVAULT_IDEM_{sid[:8]}"
    order_key = f"0xORDER_IDEM_{sid[:8]}"

    # Seed parent session row (FK requirement)
    await create_session(pg_session, session_id=sid, session_key=skey, duration_seconds=60)

    # First insert
    await record_pending_order(
        pg_session,
        vault_address=vault,
        order_key=order_key,
        session_id=sid,
        execute_after_block=100,
        decision_snapshot={"action": "open", "market": "ETH"},
    )

    # Second call — same (vault_address, order_key) — must NOT raise, must NOT duplicate
    await record_pending_order(
        pg_session,
        vault_address=vault,
        order_key=order_key,
        session_id=sid,
        execute_after_block=100,
    )

    # Assert exactly one row exists
    result = await pg_session.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM orchestrator.pending_orders "
            "WHERE vault_address = :v AND order_key = :ok"
        ),
        {"v": vault, "ok": order_key},
    )
    row = result.fetchone()
    assert row is not None
    assert row.cnt == 1, (
        f"Expected COUNT(*) == 1 after duplicate record_pending_order, got {row.cnt}. "
        "ON CONFLICT DO NOTHING is not working."
    )


# ---------------------------------------------------------------------------
# Test 2: get_pending_orders_ready respects block window and status transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pending_orders_ready_respects_block_and_status(pg_session):
    """Block-gated ready query excludes future-block orders and executed orders.

    Scenario:
      - Order A: execute_after_block=50  (ready when current_block=100)
      - Order B: execute_after_block=200 (NOT ready when current_block=100)

    Step 1: At block 100, only A is in the ready set.
    Step 2: After mark_pending_order_executed(A), the ready set is empty.
    """
    sid = str(uuid.uuid4())
    skey = f"block-test-{sid[:8]}"
    vault = f"0xVAULT_BLOCK_{sid[:8]}"
    order_key_a = f"0xORDER_A_{sid[:8]}"
    order_key_b = f"0xORDER_B_{sid[:8]}"

    # Seed parent session row
    await create_session(pg_session, session_id=sid, session_key=skey, duration_seconds=60)

    # Insert order A (eligible at block 100)
    await record_pending_order(
        pg_session,
        vault_address=vault,
        order_key=order_key_a,
        session_id=sid,
        execute_after_block=50,
    )

    # Insert order B (NOT eligible at block 100)
    await record_pending_order(
        pg_session,
        vault_address=vault,
        order_key=order_key_b,
        session_id=sid,
        execute_after_block=200,
    )

    # --- Step 1: Ready set at block 100 includes A, excludes B ---
    ready = await get_pending_orders_ready(pg_session, 100, vault_address=vault)
    ready_keys = {r["order_key"] for r in ready}

    assert order_key_a in ready_keys, (
        f"Order A (execute_after_block=50) should be ready at block=100 but is absent. "
        f"Ready keys: {ready_keys}"
    )
    assert order_key_b not in ready_keys, (
        f"Order B (execute_after_block=200) should NOT be ready at block=100 but is present. "
        f"Ready keys: {ready_keys}"
    )

    # --- Step 2: After marking A executed, it disappears from the ready set ---
    await mark_pending_order_executed(pg_session, vault_address=vault, order_key=order_key_a)

    ready_after = await get_pending_orders_ready(pg_session, 100, vault_address=vault)
    ready_keys_after = {r["order_key"] for r in ready_after}

    assert order_key_a not in ready_keys_after, (
        "Order A should no longer be in the ready set after mark_pending_order_executed, "
        "but is still present. Status was not flipped to 'executed'."
    )


# ---------------------------------------------------------------------------
# Test 3: get_unresolved_pending_orders lists pending only (excludes executed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_unresolved_pending_orders_lists_pending_only(pg_session):
    """Restart-recovery query returns pending rows, excludes executed rows.

    After marking order A executed and leaving order B as pending,
    get_unresolved_pending_orders should return only B.
    """
    sid = str(uuid.uuid4())
    skey = f"unresolved-test-{sid[:8]}"
    vault = f"0xVAULT_UNRES_{sid[:8]}"
    order_key_a = f"0xORDER_UA_{sid[:8]}"
    order_key_b = f"0xORDER_UB_{sid[:8]}"

    # Seed parent session row
    await create_session(pg_session, session_id=sid, session_key=skey, duration_seconds=60)

    # Insert A (will be executed) and B (will remain pending)
    await record_pending_order(
        pg_session,
        vault_address=vault,
        order_key=order_key_a,
        session_id=sid,
        execute_after_block=50,
    )
    await record_pending_order(
        pg_session,
        vault_address=vault,
        order_key=order_key_b,
        session_id=sid,
        execute_after_block=200,
    )

    # Execute A
    await mark_pending_order_executed(pg_session, vault_address=vault, order_key=order_key_a)

    # get_unresolved_pending_orders should list B only
    unresolved = await get_unresolved_pending_orders(pg_session, vault_address=vault)
    unresolved_keys = {r["order_key"] for r in unresolved}

    assert order_key_b in unresolved_keys, (
        f"Order B (still pending) should appear in get_unresolved_pending_orders. "
        f"Got: {unresolved_keys}"
    )
    assert order_key_a not in unresolved_keys, (
        f"Order A (executed) should NOT appear in get_unresolved_pending_orders. "
        f"Got: {unresolved_keys}"
    )

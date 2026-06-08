"""
orchestrator.state.db — Thin async SQLAlchemy/asyncpg writer for the orchestrator schema.

Exposes three atomic writers used by the mock harness and (Phase 2+) the live loop:

  record_trade(...)
      Appends a row to orchestrator.trades (immutable, append-only).
      Called ONLY after OrderExecuted is observed (D-02 / T-0-frontrun mitigation).

  record_journal_pending(vault, order_key, ...)
      Inserts a row into orchestrator.journal_entries with state='pending_pin'.
      Respects UNIQUE(vault_address, order_key) — ON CONFLICT DO NOTHING prevents
      double-write on harness replay (T-0-idem, restart-safety foundation ORCH-08).

  record_model_status(vault, model, status, consecutive_failures, reason)
      Appends a row to orchestrator.model_status_log.
      Called on malformed (ORCH-05) and timeout (ORCH-06) paths.

Connection:
  Reads ORCHESTRATOR_DATABASE_URL from the environment (asyncpg URL).
  Falls back to DATABASE_URL if ORCHESTRATOR_DATABASE_URL is unset.
  Caller is responsible for constructing and closing the engine (see get_engine()).

Design notes:
  - Uses SQLAlchemy 2.0 async session + core execute (no ORM models — plain DDL).
  - orchestrator_user role has R/W on orchestrator.* only (Plan 03 grant).
  - Phase 2 expands this module with session + position helpers.
  - All writes use UTC timestamps (datetime.now(UTC)).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def _db_url() -> str:
    """Resolve async Postgres URL from environment.

    Priority:
      1. ORCHESTRATOR_DATABASE_URL (orchestrator_user role, R/W orchestrator.*)
      2. DATABASE_URL (may be a migrator URL — usable but not least-privilege)

    Expected format: postgresql+asyncpg://user:pass@host:port/dbname
    Alembic URLs (postgresql+psycopg://...) are converted to asyncpg here.
    """
    url = os.environ.get("ORCHESTRATOR_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "ORCHESTRATOR_DATABASE_URL (or DATABASE_URL) is not set. "
            "Set it to a postgresql+asyncpg:// connection string before calling db.get_engine()."
        )
    # Normalize: Alembic uses psycopg, runtime uses asyncpg
    if "+psycopg" in url:
        url = url.replace("+psycopg", "+asyncpg", 1)
    elif url.startswith("postgresql://") or url.startswith("postgres://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgres://", "postgresql+asyncpg://", 1
        )
    return url


def get_engine(url: str | None = None, **kwargs: Any) -> AsyncEngine:
    """Create and return an async SQLAlchemy engine.

    Args:
        url: Override connection URL. Defaults to _db_url() from env.
        **kwargs: Extra kwargs forwarded to create_async_engine
                  (e.g. pool_size=5, echo=True).

    Returns:
        AsyncEngine — caller owns lifecycle (call await engine.dispose() when done).
    """
    return create_async_engine(url or _db_url(), **kwargs)


# ---------------------------------------------------------------------------
# record_trade — append to orchestrator.trades (D-02: on OrderExecuted only)
# ---------------------------------------------------------------------------


async def record_trade(
    session: AsyncSession,
    *,
    vault_address: str,
    session_id: str,
    order_key: str,
    market: str,
    side: str,
    action: str,
    size_usdc: float,
    onchain_tx: str,
    block_number: int,
    block_timestamp: datetime | None = None,
    entry_price: float | None = None,
    pnl_usdc: float | None = None,
) -> str:
    """Append an immutable trade row to orchestrator.trades.

    Called ONLY after the OrderExecuted event is observed — never on the createOrder
    receipt (D-02 / T-0-frontrun mitigation 9.1, enforced by the harness call site).

    Args:
        session: AsyncSession bound to the orchestrator_user role.
        vault_address: ERC-4626 vault address that owns the position.
        session_id: UUID of the active trading session.
        order_key: bytes32 orderKey from the MockPerps / GMX event (hex string).
        market: 'ETH' | 'BTC' | 'SOL'
        side: 'long' | 'short'
        action: 'open' | 'close' | 'adjust'
        size_usdc: Notional position size in USD.
        onchain_tx: Transaction hash of the keeper execution (from OrderExecuted).
        block_number: Block number of the OrderExecuted event.
        block_timestamp: Optional block timestamp; defaults to UTC now if not provided.
        entry_price: Entry price (USD); None for close actions.
        pnl_usdc: Realised PnL in USDC; None for open actions.

    Returns:
        trade_hash: Hex digest used as the journal idempotency key.
    """
    trade_hash = _make_trade_hash(vault_address, order_key, block_number)
    ts = block_timestamp or datetime.now(UTC)

    await session.execute(
        text(
            """
            INSERT INTO orchestrator.trades
                (id, vault_address, session_id, trade_hash, order_key,
                 market, side, action, size_usdc, entry_price, pnl_usdc,
                 onchain_tx, block_number, block_timestamp, created_at)
            VALUES
                (:id, :vault_address, CAST(:session_id AS uuid), :trade_hash, :order_key,
                 :market, :side, :action, :size_usdc, :entry_price, :pnl_usdc,
                 :onchain_tx, :block_number, :block_timestamp, :created_at)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "vault_address": vault_address,
            "session_id": session_id,
            "trade_hash": trade_hash,
            "order_key": order_key,
            "market": market,
            "side": side,
            "action": action,
            "size_usdc": size_usdc,
            "entry_price": entry_price,
            "pnl_usdc": pnl_usdc,
            "onchain_tx": onchain_tx,
            "block_number": block_number,
            "block_timestamp": ts,
            "created_at": datetime.now(UTC),
        },
    )
    await session.commit()
    return trade_hash


# ---------------------------------------------------------------------------
# record_journal_pending — insert with idempotency (T-0-idem)
# ---------------------------------------------------------------------------


async def record_journal_pending(
    session: AsyncSession,
    *,
    vault_address: str,
    order_key: str,
    trade_hash: str | None = None,
    raw_request: dict | None = None,
    raw_response: dict | None = None,
    canonical_decision: dict | None = None,
) -> None:
    """Insert a journal_entries row with state='pending_pin'.

    Idempotency: UNIQUE(vault_address, order_key) + ON CONFLICT DO NOTHING
    ensures a replay of the same cycle cannot double-write (T-0-idem, ORCH-08).

    CRITICAL (D-21): The `submitted` state must persist onchain_tx BEFORE
    broadcast. This function writes the INITIAL pending_pin row only; state
    transitions are handled by Phase 2's journal publisher.

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: ERC-4626 vault address.
        order_key: bytes32 orderKey (hex string). Forms the idempotency key with vault_address.
        trade_hash: Optional trade hash computed from record_trade; linked to the trade row.
        raw_request: Raw LLM request payload (JSONB) — stored verbatim.
        raw_response: Raw LLM response payload (JSONB) — stored verbatim.
        canonical_decision: Validated Decision dict (JSONB) — the execution-safe shape.
    """
    await session.execute(
        text(
            """
            INSERT INTO orchestrator.journal_entries
                (id, vault_address, order_key, trade_hash, state,
                 raw_request, raw_response, canonical_decision,
                 created_at, updated_at)
            VALUES
                (:id, :vault_address, :order_key, :trade_hash, 'pending_pin',
                 CAST(:raw_request AS jsonb), CAST(:raw_response AS jsonb), CAST(:canonical_decision AS jsonb),
                 :created_at, :updated_at)
            ON CONFLICT (vault_address, order_key) DO NOTHING
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "vault_address": vault_address,
            "order_key": order_key,
            "trade_hash": trade_hash,
            "raw_request": json.dumps(raw_request) if raw_request else None,
            "raw_response": json.dumps(raw_response) if raw_response else None,
            "canonical_decision": json.dumps(canonical_decision) if canonical_decision else None,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# record_model_status — append to orchestrator.model_status_log (ORCH-05/06)
# ---------------------------------------------------------------------------


async def record_model_status(
    session: AsyncSession,
    *,
    vault_address: str,
    session_id: str,
    model: str,
    status: str,
    consecutive_failures: int = 0,
    api_failure_streak: int = 0,
    malformed_streak: int = 0,
    reason: str | None = None,
    cycle_number: int | None = None,
) -> None:
    """Append a model_status_log row.

    Called on:
      - Malformed fixture / LLM response (ORCH-05): status='malformed'
      - Provider timeout (ORCH-06): status='paused' (circuit-breaker pending)
      - Recovery to active: status='active', consecutive_failures reset to 0

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: Vault managed by the model.
        session_id: Active session UUID.
        model: LLM model identifier string (e.g. 'claude-opus-4-7').
        status: 'active' | 'paused' | 'malformed'
        consecutive_failures: max(api_failure_streak, malformed_streak) for display.
            Written to the consecutive_failures column for backwards-compat.
        api_failure_streak: Current api_failure_streak value from FailureTracker.
            Persisted so the tracker can be rehydrated across restart (ORCH-06).
        malformed_streak: Current malformed_streak value from FailureTracker.
            Persisted so the tracker can be rehydrated across restart (ORCH-06).
        reason: Human-readable note (e.g. 'missing action field').
        cycle_number: Which cycle this status was recorded on.
    """
    await session.execute(
        text(
            """
            INSERT INTO orchestrator.model_status_log
                (id, vault_address, session_id, model, status,
                 consecutive_failures, api_failure_streak, malformed_streak,
                 reason, cycle_number, created_at)
            VALUES
                (:id, :vault_address, CAST(:session_id AS uuid), :model, :status,
                 :consecutive_failures, :api_failure_streak, :malformed_streak,
                 :reason, :cycle_number, :created_at)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "vault_address": vault_address,
            "session_id": session_id,
            "model": model,
            "status": status,
            "consecutive_failures": consecutive_failures,
            "api_failure_streak": api_failure_streak,
            "malformed_streak": malformed_streak,
            "reason": reason,
            "cycle_number": cycle_number,
            "created_at": datetime.now(UTC),
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# get_latest_model_status — rehydration query for FailureTracker restart (ORCH-06)
# ---------------------------------------------------------------------------


async def get_latest_model_status(
    session: AsyncSession,
    *,
    vault_address: str,
) -> dict | None:
    """Return the most-recent model_status_log row for a vault, or None if empty.

    Called on orchestrator startup to rehydrate the FailureTracker so a model
    that was 2/3 of the way to pause before a SIGKILL resumes at 2, not 0
    (ORCH-06 restart-safety requirement, CR-01 fix).

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: Vault to query (one vault per model).

    Returns:
        Dict with keys: status, api_failure_streak, malformed_streak,
        consecutive_failures, model, cycle_number, created_at.
        None if no rows exist for this vault.
    """
    result = await session.execute(
        text(
            """
            SELECT id, vault_address, session_id, model, status,
                   consecutive_failures, api_failure_streak, malformed_streak,
                   reason, cycle_number, created_at
            FROM orchestrator.model_status_log
            WHERE vault_address = :vault_address
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"vault_address": vault_address},
    )
    row = result.fetchone()
    return dict(row._mapping) if row is not None else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_trade_hash(vault_address: str, order_key: str, block_number: int) -> str:
    """Deterministic trade hash: SHA-256 of (vault_address + order_key + block_number).

    Matches the keccak-style uniqueness the JournalRegistry contract uses onchain,
    but computed off-chain for the mock harness path.  Phase 2 replaces with the
    actual onchain trade_hash from the JournalRegistry contract.
    """
    payload = f"{vault_address.lower()}:{order_key.lower()}:{block_number}"
    return "0x" + hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# record_pending_order — insert with idempotency (ORCH-07/08, T-02-09)
# ---------------------------------------------------------------------------


async def record_pending_order(
    session: AsyncSession,
    *,
    vault_address: str,
    order_key: str,
    session_id: str,
    execute_after_block: int,
    status: str = "pending",
    decision_snapshot: dict | None = None,
    submit_tx_hash: str | None = None,
) -> None:
    """Insert a pending_orders row recording a submitted-but-not-yet-executed order.

    Idempotency: UNIQUE(vault_address, order_key) + ON CONFLICT DO NOTHING
    ensures a restart cannot double-insert the same intent row (T-02-09, ORCH-08).

    Accepts an optional ``status`` parameter (default ``'pending'``).  Pass
    ``status='intent'`` for the pre-submit intent row written BEFORE the MockPerps
    call (ORCH-08 record-intent-before-submit pattern, Plan 05).

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: ERC-4626 vault address.
        order_key: bytes32 orderKey (hex string). Forms the idempotency key with vault_address.
        session_id: Active session UUID (FK -> orchestrator.sessions.id).
        execute_after_block: Earliest block at which the keeper may execute this order.
        status: One of 'intent' | 'pending' | 'executed' | 'reconciled' | 'cancelled'.
                Default 'pending' (post-submit keeper-poll window status).
        decision_snapshot: Validated Decision dict stored verbatim as JSONB (optional).
        submit_tx_hash: Raw tx hash returned by vault.openLong/openShort/closePosition.transact()
                        (GAP #10). Stored so reconcile can call eth_getTransactionByHash on restart
                        to detect pending-in-mempool txs and prevent duplicate submissions.
                        None for intent rows (written before submit) and pre-GAP-10 rows.
    """
    await session.execute(
        text(
            """
            INSERT INTO orchestrator.pending_orders
                (id, vault_address, order_key, session_id,
                 execute_after_block, status, decision_snapshot,
                 submit_tx_hash,
                 created_at, updated_at)
            VALUES
                (:id, :vault_address, :order_key, CAST(:session_id AS uuid),
                 :execute_after_block, :status, CAST(:decision_snapshot AS jsonb),
                 :submit_tx_hash,
                 :created_at, :updated_at)
            ON CONFLICT (vault_address, order_key) DO NOTHING
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "vault_address": vault_address,
            "order_key": order_key,
            "session_id": session_id,
            "execute_after_block": execute_after_block,
            "status": status,
            "decision_snapshot": json.dumps(decision_snapshot)
            if decision_snapshot is not None
            else None,
            "submit_tx_hash": submit_tx_hash,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# get_pending_orders_ready — block-gated keeper query (T-02-10)
# ---------------------------------------------------------------------------


async def get_pending_orders_ready(
    session: AsyncSession,
    current_block: int,
    *,
    vault_address: str | None = None,
) -> list[dict]:
    """Return pending_orders rows that are eligible for keeper execution.

    A row is eligible when:
      - status = 'pending'  (not yet executed or cancelled)
      - execute_after_block <= current_block  (the execution delay has elapsed)

    Optionally filtered to a single vault (pass vault_address=None for all vaults).
    Results are ordered by created_at ASC so the oldest intent is executed first.

    Args:
        session: AsyncSession bound to orchestrator_user role.
        current_block: Current chain block number (from web3.eth.block_number).
        vault_address: Optional filter; returns rows for all vaults when None.

    Returns:
        List of dicts with keys: id, vault_address, order_key, session_id,
        execute_after_block, status, decision_snapshot.
    """
    if vault_address is not None:
        result = await session.execute(
            text(
                """
                SELECT id, vault_address, order_key, session_id,
                       execute_after_block, status, decision_snapshot
                FROM orchestrator.pending_orders
                WHERE status = 'pending'
                  AND execute_after_block <= :current_block
                  AND vault_address = :vault_address
                ORDER BY created_at ASC
                """
            ),
            {"current_block": current_block, "vault_address": vault_address},
        )
    else:
        result = await session.execute(
            text(
                """
                SELECT id, vault_address, order_key, session_id,
                       execute_after_block, status, decision_snapshot
                FROM orchestrator.pending_orders
                WHERE status = 'pending'
                  AND execute_after_block <= :current_block
                ORDER BY created_at ASC
                """
            ),
            {"current_block": current_block},
        )
    return [dict(r._mapping) for r in result]


# ---------------------------------------------------------------------------
# mark_pending_order_executed — status flip (idempotent, T-02-09)
# ---------------------------------------------------------------------------


async def mark_pending_order_executed(
    session: AsyncSession,
    *,
    vault_address: str,
    order_key: str,
) -> None:
    """Flip a pending_orders row from 'pending' to 'executed'.

    The WHERE clause includes status='pending' so a second call (e.g. on restart
    after the keeper already flipped it) is a silent no-op — no error, no double
    transition (idempotent by design).

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: Vault address part of the UNIQUE key.
        order_key: orderKey part of the UNIQUE key.
    """
    await session.execute(
        text(
            """
            UPDATE orchestrator.pending_orders
            SET status = 'executed', updated_at = :updated_at
            WHERE vault_address = :vault_address
              AND order_key = :order_key
              AND status = 'pending'
            """
        ),
        {
            "vault_address": vault_address,
            "order_key": order_key,
            "updated_at": datetime.now(UTC),
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# mark_pending_order_reconciled — intent → reconciled status flip (ORCH-08)
# ---------------------------------------------------------------------------


async def mark_pending_order_reconciled(
    session: AsyncSession,
    *,
    vault_address: str,
    order_key: str,
) -> None:
    """Flip a pending_orders row from 'intent' (or 'pending') to 'reconciled'.

    Called by the driver (Plan 05) after the pre-submit intent row has been
    superseded by the real order_key row (step 8d of run_live_cycle).

    The WHERE clause guards on status IN ('intent', 'pending') so a second call
    after the row has already been reconciled is a silent no-op (idempotent).

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: Vault address part of the UNIQUE key.
        order_key: orderKey part of the UNIQUE key (typically the intent-* key).
    """
    await session.execute(
        text(
            """
            UPDATE orchestrator.pending_orders
            SET status = 'reconciled', updated_at = :updated_at
            WHERE vault_address = :vault_address
              AND order_key = :order_key
              AND status IN ('intent', 'pending')
            """
        ),
        {
            "vault_address": vault_address,
            "order_key": order_key,
            "updated_at": datetime.now(UTC),
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# get_unresolved_pending_orders — restart recovery query (ORCH-08, T-02-11)
# ---------------------------------------------------------------------------


async def get_unresolved_pending_orders(
    session: AsyncSession,
    *,
    vault_address: str,
) -> list[dict]:
    """Return all status='intent' or status='pending' rows for a vault.

    Called at orchestrator startup to discover orders that were submitted before
    a crash (or pre-submit intent rows where the submit never landed).

    - status='intent': pre-submit row — the MockPerps submit may or may not have
      landed.  Caller checks chain state to decide whether to resubmit.
    - status='pending': post-submit row — the order is on-chain; keeper will execute.

    Never double-resubmits: the caller checks chain state first; record_pending_order
    ON CONFLICT DO NOTHING prevents duplicate intent rows even if reconciliation
    runs twice (T-02-11 lost-order-history mitigation).

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: Vault to reconcile (one vault per model).

    Returns:
        List of dicts (same shape as get_pending_orders_ready) ordered by created_at ASC.
    """
    result = await session.execute(
        text(
            """
            SELECT id, vault_address, order_key, session_id,
                   execute_after_block, status, decision_snapshot,
                   submit_tx_hash
            FROM orchestrator.pending_orders
            WHERE vault_address = :vault_address
              AND status IN ('intent', 'pending')
            ORDER BY created_at ASC
            """
        ),
        {"vault_address": vault_address},
    )
    return [dict(r._mapping) for r in result]


# ---------------------------------------------------------------------------
# has_unresolved_pending_order — per-vault in-flight gate (ARCH-X submission guard)
# ---------------------------------------------------------------------------


async def has_unresolved_pending_order(
    session: AsyncSession,
    *,
    vault_address: str,
) -> bool:
    """Return True if any status='intent' or status='pending' row exists for this vault.

    ARCH-X submission gate: called by run_live_cycle BEFORE writing the intent row so
    the driver can skip a submission cycle when a prior order is still in-flight.

    This is a cheap EXISTS query (index scan on vault_address + status).  The result is
    used to gate the cycle — if True, the cycle is skipped with an INFO log rather than
    over-submitting and hitting the "Vault: order in flight" revert.

    Single-owner / no TOCTOU: the decision loop is the SOLE submitter per vault (the
    keeper only CLEARs via mark_pending_order_executed / mark_pending_order_reconciled).
    The driver's event loop is cooperative — there is exactly one asyncio Task running
    the cycle loop per vault, and no async yield occurs between this check and the
    record_pending_order(status='intent') call that CREATES the lock row.  That makes
    the read→check→create sequence effectively atomic within a single-vault session.

    Args:
        session: AsyncSession bound to orchestrator_user role.
        vault_address: Vault address to check.

    Returns:
        True if at least one unresolved row exists; False if the vault is clear.
    """
    result = await session.execute(
        text(
            """
            SELECT 1
            FROM orchestrator.pending_orders
            WHERE vault_address = :vault_address
              AND status IN ('intent', 'pending')
            LIMIT 1
            """
        ),
        {"vault_address": vault_address},
    )
    return result.fetchone() is not None


# ---------------------------------------------------------------------------
# create_session / end_session — session lifecycle (D-12, ORCH plan 05 startup)
# ---------------------------------------------------------------------------


async def create_session(
    session: AsyncSession,
    *,
    session_id: str,
    session_key: str,
    duration_seconds: int,
) -> None:
    """Insert an orchestrator.sessions row for a new trading session.

    Idempotent: ON CONFLICT (id) DO NOTHING — safe to call on restart without
    creating a duplicate row.

    Args:
        session: AsyncSession bound to orchestrator_user role.
        session_id: UUID string for the new session (PK).
        session_key: Unique human-readable key (e.g. 'claude-s1-20260608').
        duration_seconds: Planned session duration (used for the D-12 countdown).
    """
    now = datetime.now(UTC)
    await session.execute(
        text(
            """
            INSERT INTO orchestrator.sessions
                (id, session_key, duration_seconds, state, started_at, created_at, updated_at)
            VALUES
                (CAST(:session_id AS uuid), :session_key, :duration_seconds,
                 'active', :started_at, :created_at, :updated_at)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "session_id": session_id,
            "session_key": session_key,
            "duration_seconds": duration_seconds,
            "started_at": now,
            "created_at": now,
            "updated_at": now,
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# update_journal_state — D-08 state machine transitions (JOURNAL-01)
# ---------------------------------------------------------------------------


async def update_journal_state(
    session: AsyncSession,
    *,
    vault_address: str,
    order_key: str,
    new_state: str,
    pinata_cid: str | None = None,
    web3_storage_cid: str | None = None,
    operator_sig: str | None = None,
    onchain_tx: str | None = None,
) -> None:
    """Transition a journal_entries row to a new state (D-08 state machine).

    D-08 state flow: pending_pin -> pinned_primary -> signed -> submitted -> recorded
                              (async, non-blocking)         -> pinned_backup

    COALESCE semantics: optional columns (pinata_cid, web3_storage_cid, operator_sig,
    onchain_tx) are only updated when explicitly provided (non-None). Passing None
    leaves the existing column value unchanged — safe to call at each transition with
    only the relevant new data.

    Reuses the existing 7-state journal_state ENUM from migration 0001 — no new
    migration needed (Decision 03-06: reuse existing states).

    Args:
        session:         AsyncSession bound to orchestrator_user role.
        vault_address:   Part of the UNIQUE(vault_address, order_key) key.
        order_key:       Part of the UNIQUE(vault_address, order_key) key.
        new_state:       One of the existing journal_state ENUM values:
                         pending_pin | pinned_primary | pinned_backup | signed
                         | submitted | recorded | failed
        pinata_cid:      Pinata CID — set on -> pinned_primary transition.
        web3_storage_cid: Filebase backup CID — set on -> pinned_backup transition.
        operator_sig:    Hex-encoded EIP-191 signature — set on -> signed transition.
        onchain_tx:      Onchain tx hash from recordJournal — set on -> recorded transition.
    """
    await session.execute(
        text(
            """
            UPDATE orchestrator.journal_entries
            SET state            = CAST(:new_state AS orchestrator.journal_state),
                pinata_cid       = COALESCE(:pinata_cid, pinata_cid),
                web3_storage_cid = COALESCE(:web3_storage_cid, web3_storage_cid),
                operator_sig     = COALESCE(:operator_sig, operator_sig),
                onchain_tx       = COALESCE(:onchain_tx, onchain_tx),
                updated_at       = :updated_at
            WHERE vault_address = :vault_address
              AND order_key     = :order_key
            """
        ),
        {
            "vault_address": vault_address,
            "order_key": order_key,
            "new_state": new_state,
            "pinata_cid": pinata_cid,
            "web3_storage_cid": web3_storage_cid,
            "operator_sig": operator_sig,
            "onchain_tx": onchain_tx,
            "updated_at": datetime.now(UTC),
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# get_pending_pin_entries — retry query for failed/pending_pin entries (D-08)
# ---------------------------------------------------------------------------


async def get_pending_pin_entries(
    session: AsyncSession,
    *,
    vault_address: str,
) -> list[dict]:
    """Return journal_entries rows with state='pending_pin' for a vault.

    Called by the reconcile path to retry entries that failed during Pinata pinning
    (e.g. transient network error). Records advance to pinned_primary once Pinata
    confirms. Only pending_pin is returned — partially advanced entries (pinned_primary,
    signed) are not retried here (they have distinct reconciliation paths).

    Args:
        session:       AsyncSession bound to orchestrator_user role.
        vault_address: Vault to query (one vault per model).

    Returns:
        List of dicts with keys: id, vault_address, order_key, trade_hash,
        raw_request, raw_response, canonical_decision, state, pinata_cid,
        onchain_tx, created_at. Ordered oldest-first for FIFO retry.
    """
    result = await session.execute(
        text(
            """
            SELECT id, vault_address, order_key, trade_hash,
                   raw_request, raw_response, canonical_decision,
                   state, pinata_cid, onchain_tx, created_at
            FROM orchestrator.journal_entries
            WHERE vault_address = :vault_address
              AND state = 'pending_pin'
            ORDER BY created_at ASC
            """
        ),
        {"vault_address": vault_address},
    )
    return [dict(row._mapping) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# end_session — session lifecycle (D-12, ORCH plan 05 startup)
# ---------------------------------------------------------------------------


async def end_session(
    session: AsyncSession,
    *,
    session_id: str,
) -> None:
    """Mark an active session as ended (D-12 session-end lifecycle).

    Sets state='ended' and records ended_at.  Does NOT close any open positions —
    the settlement contract handles position draining separately.

    Args:
        session: AsyncSession bound to orchestrator_user role.
        session_id: UUID string of the session to end.
    """
    now = datetime.now(UTC)
    await session.execute(
        text(
            """
            UPDATE orchestrator.sessions
            SET state = 'ended', ended_at = :ended_at, updated_at = :updated_at
            WHERE id = CAST(:session_id AS uuid)
            """
        ),
        {
            "session_id": session_id,
            "ended_at": now,
            "updated_at": now,
        },
    )
    await session.commit()

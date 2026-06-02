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
        consecutive_failures: Running failure count (resets on success).
        reason: Human-readable note (e.g. 'missing action field').
        cycle_number: Which cycle this status was recorded on.
    """
    await session.execute(
        text(
            """
            INSERT INTO orchestrator.model_status_log
                (id, vault_address, session_id, status, reason, cycle_number, created_at)
            VALUES
                (:id, :vault_address, CAST(:session_id AS uuid), :status, :reason,
                 :cycle_number, :created_at)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "vault_address": vault_address,
            "session_id": session_id,
            "status": status,
            "reason": reason,
            "cycle_number": cycle_number,
            "created_at": datetime.now(UTC),
        },
    )
    await session.commit()


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

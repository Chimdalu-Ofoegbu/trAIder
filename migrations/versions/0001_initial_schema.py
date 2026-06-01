"""Initial schema -- orchestrator.* + backend.* tables, journal_state ENUM,
BRIN/B-tree indexes, materialized views, and NAV refresh trigger.

Revision ID: 0001
Revises:
Create Date: 2026-06-01 00:00:00.000000

Design decisions implemented here (D-19 / D-20 / D-21 / D-22):
  - Every op carries explicit schema= ("orchestrator" or "backend").
  - BRIN indexes, materialized views, ENUMs, triggers -> op.execute() (hand-written).
  - journal_entries is the three-phase-commit state machine (D-21).
  - UNIQUE(vault_address, order_key) is the idempotency key for journal recovery.
  - CRITICAL (D-21): `submitted` state persists onchain_tx BEFORE broadcast.
    Recovery must query chain status before resubmit; never blindly resubmit.
  - dashboard_model_state and dashboard_session_state are MATERIALIZED VIEWs
    (not regular views) because backend reads them repeatedly per WS tick.
  - The trigger on orchestrator.nav_snapshots refreshes dashboard_model_state
    on every insert, keeping the WS snapshot current.
  - Tables that reference orchestrator.journal_state ENUM use raw op.execute()
    DDL to avoid SQLAlchemy sa.Enum emitting a spurious CREATE TYPE in offline
    (--sql) mode even when create_type=False is set.

downgrade() reverses in dependency order:
  trigger -> function -> mat-views -> backend tables -> orchestrator tables -> ENUM
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. journal_state ENUM -- must be created BEFORE journal_entries table.
    #    Seven states for the three-phase-commit state machine (D-21):
    #      pending_pin  -> pinned_primary -> pinned_backup -> signed
    #                   -> submitted -> recorded (terminal)
    #                              -> failed    (terminal)
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE TYPE orchestrator.journal_state AS ENUM (
            'pending_pin',
            'pinned_primary',
            'pinned_backup',
            'signed',
            'submitted',
            'recorded',
            'failed'
        )
        """
    )

    # -----------------------------------------------------------------------
    # 2. orchestrator.sessions -- top-level 72-hour trading session
    # -----------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_key", sa.Text(), nullable=False, unique=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("fork_block_number", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 3. orchestrator.vaults -- per-model ERC-4626 vault
    # -----------------------------------------------------------------------
    op.create_table(
        "vaults",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", sa.UUID(as_uuid=True), sa.ForeignKey("orchestrator.sessions.id"), nullable=False),
        sa.Column("vault_address", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_provider", sa.Text(), nullable=False),
        sa.Column("initial_usdc", sa.Numeric(precision=36, scale=18), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("session_id", "vault_address", name="uq_vaults_session_addr"),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 4. orchestrator.positions -- open perpetual positions per vault
    # -----------------------------------------------------------------------
    op.create_table(
        "positions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vault_address", sa.Text(), nullable=False, index=True),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("size_usdc", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("collateral_usdc", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("leverage", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("position_key", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 5. orchestrator.trades -- executed trades (immutable, append-only)
    # -----------------------------------------------------------------------
    op.create_table(
        "trades",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vault_address", sa.Text(), nullable=False),
        sa.Column("session_id", sa.UUID(as_uuid=True), sa.ForeignKey("orchestrator.sessions.id"), nullable=False),
        sa.Column("trade_hash", sa.Text(), nullable=False),
        sa.Column("order_key", sa.Text(), nullable=True),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("size_usdc", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=36, scale=18), nullable=True),
        sa.Column("pnl_usdc", sa.Numeric(precision=36, scale=18), nullable=True),
        sa.Column("onchain_tx", sa.Text(), nullable=True),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("block_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 6. orchestrator.journal_entries -- three-phase-commit state machine (D-21)
    #
    #    CRITICAL: `submitted` state must persist onchain_tx BEFORE broadcast.
    #    Recovery: query chain for tx status before resubmit.
    #    Never blindly resubmit a `submitted` entry (risk of double-execution).
    #
    #    Idempotency key: UNIQUE(vault_address, order_key)
    #
    #    Using raw op.execute() to reference orchestrator.journal_state ENUM
    #    directly. SQLAlchemy sa.Enum(create_type=False) still emits CREATE TYPE
    #    in offline (--sql) mode; raw DDL avoids that spurious statement.
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE orchestrator.journal_entries (
            id                  UUID DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
            vault_address       TEXT NOT NULL,
            order_key           TEXT NOT NULL,
            trade_hash          TEXT,
            state               orchestrator.journal_state NOT NULL DEFAULT 'pending_pin',
            raw_request         JSONB,
            raw_response        JSONB,
            canonical_decision  JSONB,
            pinata_cid          TEXT,
            web3_storage_cid    TEXT,
            operator_sig        TEXT,
            onchain_tx          TEXT,
            attempt_count       SMALLINT NOT NULL DEFAULT 0,
            last_error          TEXT,
            created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            CONSTRAINT uq_journal_vault_order UNIQUE (vault_address, order_key)
        )
        """
        # CRITICAL (D-21): `submitted` state must persist onchain_tx BEFORE broadcast.
        # On recovery: query chain for this tx before resubmitting.
    )

    # -----------------------------------------------------------------------
    # 7. orchestrator.model_decisions -- raw LLM decision payloads per cycle
    # -----------------------------------------------------------------------
    op.create_table(
        "model_decisions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vault_address", sa.Text(), nullable=False, index=True),
        sa.Column("session_id", sa.UUID(as_uuid=True), sa.ForeignKey("orchestrator.sessions.id"), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("raw_request", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("raw_response", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("canonical_decision", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("response_latency_ms", sa.Integer(), nullable=True),
        sa.Column("validation_status", sa.Text(), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 8. orchestrator.nav_snapshots -- NAV time-series (append-only, D-20)
    #    BRIN index on (vault_address, block_timestamp) added below.
    # -----------------------------------------------------------------------
    op.create_table(
        "nav_snapshots",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vault_address", sa.Text(), nullable=False),
        sa.Column("session_id", sa.UUID(as_uuid=True), sa.ForeignKey("orchestrator.sessions.id"), nullable=False),
        sa.Column("nav_per_token_1e18", sa.Numeric(precision=36, scale=0), nullable=False),
        sa.Column("total_assets_usdc", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("total_supply", sa.Numeric(precision=36, scale=0), nullable=True),
        sa.Column("chainlink_eth_price", sa.Numeric(precision=36, scale=18), nullable=True),
        sa.Column("chainlink_btc_price", sa.Numeric(precision=36, scale=18), nullable=True),
        sa.Column("chainlink_sol_price", sa.Numeric(precision=36, scale=18), nullable=True),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("block_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 9. orchestrator.journal_state_log -- append-only transition history (debug-only)
    #    NOT a source of truth; used only for debugging state machine transitions.
    #    Uses op.execute to reference orchestrator.journal_state ENUM directly.
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE orchestrator.journal_state_log (
            id              UUID DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
            journal_entry_id UUID NOT NULL
                REFERENCES orchestrator.journal_entries (id),
            from_state      orchestrator.journal_state,
            to_state        orchestrator.journal_state NOT NULL,
            transitioned_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            note            TEXT
        )
        """
    )

    # -----------------------------------------------------------------------
    # 10. orchestrator.model_status_log -- paused/malformed model state (ORCH-06)
    # -----------------------------------------------------------------------
    op.create_table(
        "model_status_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vault_address", sa.Text(), nullable=False, index=True),
        sa.Column("session_id", sa.UUID(as_uuid=True), sa.ForeignKey("orchestrator.sessions.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("cycle_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 11. orchestrator.event_log -- structured operational logs (D-69)
    # -----------------------------------------------------------------------
    op.create_table(
        "event_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default="INFO"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("vault_address", sa.Text(), nullable=True),
        sa.Column("session_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        schema="orchestrator",
    )

    # -----------------------------------------------------------------------
    # 12. BRIN indexes (D-20) -- cheap range scans on append-only time-series.
    #     B-tree on trades(trade_hash) for journal lookups.
    # -----------------------------------------------------------------------
    op.execute(
        "CREATE INDEX ix_nav_brin ON orchestrator.nav_snapshots "
        "USING BRIN (vault_address, block_timestamp)"
    )
    op.execute(
        "CREATE INDEX ix_trades_brin ON orchestrator.trades "
        "USING BRIN (vault_address, block_timestamp)"
    )
    op.execute(
        "CREATE INDEX ix_trades_hash ON orchestrator.trades (trade_hash)"
    )

    # -----------------------------------------------------------------------
    # 13. backend.websocket_sessions -- active WebSocket connections (ephemeral)
    # -----------------------------------------------------------------------
    op.create_table(
        "websocket_sessions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("connection_id", sa.Text(), nullable=False, unique=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("wallet_address", sa.Text(), nullable=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("last_ping_at", sa.DateTime(timezone=True), nullable=True),
        schema="backend",
    )

    # -----------------------------------------------------------------------
    # 14. backend.verifier_replay_log -- verifier CLI replay results
    # -----------------------------------------------------------------------
    op.create_table(
        "verifier_replay_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("journal_entry_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("original_cid", sa.Text(), nullable=True),
        sa.Column("replayed_response", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("diff_summary", sa.Text(), nullable=True),
        schema="backend",
    )

    # -----------------------------------------------------------------------
    # 15. backend.dashboard_model_state -- MATERIALIZED VIEW
    #     Updated by the refresh trigger on orchestrator.nav_snapshots.
    #     Phase 0: minimal SELECT covering the data the WS CurrentState snapshot needs.
    #     Phase 5 enriches with position/trade aggregates.
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE MATERIALIZED VIEW backend.dashboard_model_state AS
        SELECT
            v.vault_address,
            v.model_name,
            v.model_provider,
            v.session_id,
            ns.nav_per_token_1e18,
            ns.total_assets_usdc,
            ns.block_timestamp AS nav_at,
            ns.created_at     AS snapshot_at
        FROM orchestrator.vaults v
        LEFT JOIN LATERAL (
            SELECT
                nav_per_token_1e18,
                total_assets_usdc,
                block_timestamp,
                created_at
            FROM orchestrator.nav_snapshots ns2
            WHERE ns2.vault_address = v.vault_address
            ORDER BY ns2.created_at DESC
            LIMIT 1
        ) ns ON true
        WITH NO DATA
        """
    )

    # -----------------------------------------------------------------------
    # 16. backend.dashboard_session_state -- MATERIALIZED VIEW
    #     Session-level aggregates for the Coliseum header row.
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE MATERIALIZED VIEW backend.dashboard_session_state AS
        SELECT
            s.id         AS session_id,
            s.session_key,
            s.state      AS session_state,
            s.started_at,
            s.ended_at,
            COUNT(DISTINCT v.id) AS vault_count
        FROM orchestrator.sessions s
        LEFT JOIN orchestrator.vaults v ON v.session_id = s.id
        GROUP BY s.id, s.session_key, s.state, s.started_at, s.ended_at
        WITH NO DATA
        """
    )

    # -----------------------------------------------------------------------
    # 17. backend.refresh_model_state() -- trigger function that refreshes the
    #     dashboard_model_state mat-view whenever a NAV snapshot is inserted.
    #     Non-concurrent refresh is fine for Phase 0 (no UNIQUE index on the
    #     mat-view to enable CONCURRENTLY; add one in Phase 5 if needed).
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION backend.refresh_model_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW backend.dashboard_model_state;
            RETURN NULL;
        END;
        $$
        """
    )

    # -----------------------------------------------------------------------
    # 18. Trigger: AFTER INSERT on orchestrator.nav_snapshots
    #     -> calls backend.refresh_model_state()
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE TRIGGER trg_refresh_model_state
        AFTER INSERT ON orchestrator.nav_snapshots
        FOR EACH STATEMENT
        EXECUTE FUNCTION backend.refresh_model_state()
        """
    )


def downgrade() -> None:
    # Reverse in strict dependency order:
    # trigger -> function -> mat-views -> backend tables -> orchestrator tables -> ENUM

    # 18. Drop trigger
    op.execute(
        "DROP TRIGGER IF EXISTS trg_refresh_model_state ON orchestrator.nav_snapshots"
    )

    # 17. Drop trigger function
    op.execute(
        "DROP FUNCTION IF EXISTS backend.refresh_model_state()"
    )

    # 15-16. Drop materialized views
    op.execute(
        "DROP MATERIALIZED VIEW IF EXISTS backend.dashboard_model_state"
    )
    op.execute(
        "DROP MATERIALIZED VIEW IF EXISTS backend.dashboard_session_state"
    )

    # 14. Drop backend tables
    op.drop_table("verifier_replay_log", schema="backend")
    op.drop_table("websocket_sessions", schema="backend")

    # 11. Drop orchestrator tables in reverse FK order
    op.drop_table("event_log", schema="orchestrator")
    op.drop_table("model_status_log", schema="orchestrator")
    op.execute("DROP TABLE IF EXISTS orchestrator.journal_state_log")
    op.drop_table("nav_snapshots", schema="orchestrator")
    op.drop_table("model_decisions", schema="orchestrator")
    op.execute("DROP TABLE IF EXISTS orchestrator.journal_entries")
    op.drop_table("trades", schema="orchestrator")
    op.drop_table("positions", schema="orchestrator")
    op.drop_table("vaults", schema="orchestrator")
    op.drop_table("sessions", schema="orchestrator")

    # 1. Drop the ENUM type last (all columns referencing it must be gone first)
    op.execute("DROP TYPE IF EXISTS orchestrator.journal_state")

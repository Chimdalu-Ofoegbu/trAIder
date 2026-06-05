"""pending_orders table -- restart-safe order tracking (ORCH-07/08).

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-05 00:00:00.000000

Design decisions implemented here:
  - raw op.execute() DDL (not op.create_table) to avoid the ENUM double-CREATE
    issue and keep the inline UNIQUE constraint pattern (mirrors journal_entries
    in 0001_initial_schema.py lines 223-247).
  - UNIQUE(vault_address, order_key) is the idempotency key consumed by
    Plan 03's ON CONFLICT DO NOTHING and the record-intent-before-submit
    intent row (ORCH-08).
  - FK on session_id -> orchestrator.sessions(id) keeps referential integrity.
  - status values (plain TEXT, no ENUM -- mirrors sessions.state pattern):
      'intent'      -- pre-submit record-intent row (ORCH-08)
      'pending'     -- submitted to MockPerps, awaiting keeper execution
      'executed'    -- keeper-executed, order_key confirmed onchain
      'reconciled'  -- intent row promoted to real on-chain order_key
      'cancelled'   -- order was cancelled / not executed
  - Grants mirror 0002_roles_and_grants.py:
      orchestrator_user: SELECT, INSERT, UPDATE (no DELETE at runtime)
      backend_user: SELECT only (least privilege, STRIDE T-02-01)

downgrade() drops the table. Safe to re-run (IF EXISTS).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # orchestrator.pending_orders -- restart-safe order intent/state store
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE orchestrator.pending_orders (
            id                    UUID DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
            vault_address         TEXT NOT NULL,
            order_key             TEXT NOT NULL,
            session_id            UUID NOT NULL REFERENCES orchestrator.sessions(id),
            execute_after_block   BIGINT NOT NULL,
            status                TEXT NOT NULL DEFAULT 'pending',
            decision_snapshot     JSONB,
            created_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            updated_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            CONSTRAINT uq_pending_order_vault_key UNIQUE (vault_address, order_key)
        )
        """
    )

    # -----------------------------------------------------------------------
    # Grants (mirrors 0002_roles_and_grants.py grant pattern)
    # orchestrator_user: R/W (no DELETE -- STRIDE T-02-01 least privilege)
    # backend_user: SELECT only
    # -----------------------------------------------------------------------
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON orchestrator.pending_orders TO orchestrator_user"
    )
    op.execute("GRANT SELECT ON orchestrator.pending_orders TO backend_user")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS orchestrator.pending_orders")

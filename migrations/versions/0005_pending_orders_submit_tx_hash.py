"""Add submit_tx_hash column to pending_orders (GAP #10 duplicate-prevention).

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-08 00:00:00.000000

GAP #10: On restart, reconcile_pending_orders must check whether the original
submit tx is still in the mempool (or already mined) before marking an order
resubmittable. Without this check, the original AND a resubmit both mine → dup.

The submit_tx_hash column stores the tx hash returned by vault.openLong/openShort/
closePosition.transact() IMMEDIATELY after submit. On restart, reconcile calls
eth_getTransactionByHash on this hash — if the tx exists (pending OR mined),
the resubmit is suppressed.

Column semantics:
  - Nullable TEXT (not all rows will have a tx hash — pre-GAP-10 rows won't).
  - Populated in run_live_cycle step 8c immediately after .transact() returns.
  - NOT the OrderCreated event order_key — that is in the order_key column.
    This is the raw submit tx hash used to check mempool presence.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE orchestrator.pending_orders
        ADD COLUMN IF NOT EXISTS submit_tx_hash TEXT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE orchestrator.pending_orders
        DROP COLUMN IF EXISTS submit_tx_hash
        """
    )

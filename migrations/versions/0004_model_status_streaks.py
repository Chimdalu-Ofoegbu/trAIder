"""model_status_log streak columns -- persist pause counters for ORCH-06 restart-safety.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05 00:00:00.000000

Design decisions implemented here (CR-01 fix):
  - record_model_status accepts model + consecutive_failures but neither column
    existed in the table or INSERT — silently dropped (CR-01 in 02-REVIEW.md).
  - Add model TEXT + api_failure_streak + malformed_streak INTEGER columns so
    the FailureTracker state can be rehydrated across SIGKILL+restart (ORCH-06).
  - consecutive_failures stored as max(api_failure_streak, malformed_streak) for
    backwards-compat with existing callers that read the single combined counter;
    both individual streaks are also persisted for precise rehydration.
  - Grants mirror 0002_roles_and_grants.py + 0003_pending_orders.py patterns.

downgrade() reverses the four ALTER TABLE statements with DROP COLUMN (safe).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Add model + streak columns to orchestrator.model_status_log (CR-01)
    # -----------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE orchestrator.model_status_log
            ADD COLUMN IF NOT EXISTS model TEXT,
            ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS api_failure_streak INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS malformed_streak INTEGER NOT NULL DEFAULT 0
        """
    )

    # -----------------------------------------------------------------------
    # orchestrator_user already has INSERT/UPDATE from 0002_roles_and_grants;
    # no new grants needed — columns inherit table-level permissions.
    # -----------------------------------------------------------------------


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE orchestrator.model_status_log
            DROP COLUMN IF EXISTS model,
            DROP COLUMN IF EXISTS consecutive_failures,
            DROP COLUMN IF EXISTS api_failure_streak,
            DROP COLUMN IF EXISTS malformed_streak
        """
    )

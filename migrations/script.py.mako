"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Migration rules (D-22):
  - Every op MUST carry explicit schema= ("orchestrator" or "backend").
  - BRIN indexes, materialized views, ENUMs, and triggers are hand-written
    via op.execute() — autogenerate cannot represent them.
  - downgrade() must reverse ALL changes in dependency order.
  - Do NOT leave any trAIder objects in the `public` schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    # --- Write your upgrade ops here ---
    # All ops must carry explicit schema= (e.g. schema="orchestrator").
    # Use op.execute() for BRIN indexes, ENUMs, mat-views, triggers.
    pass


def downgrade() -> None:
    # --- Reverse upgrade ops in dependency order ---
    # Drop triggers → functions → mat-views → tables → ENUMs.
    pass

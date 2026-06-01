"""Least-privilege roles + grants (D-19).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-01 00:00:01.000000

Three roles (D-19):
  - orchestrator_user: R/W on orchestrator.*, NO access to backend.*
  - backend_user: R-only on orchestrator.*, R/W on backend.*
  - migrator_user: DDL authority (the role Alembic connects as)

Trust boundary (STRIDE T-0-acl):
  A compromised backend_user cannot tamper with source-of-truth orchestrator tables.
  orchestrator_user has explicitly REVOKED access to the backend schema.
  migrator_user is NEVER used by running services — DDL-only, never runtime.

Role creation is idempotent (DO $$ IF NOT EXISTS) so re-running downgrade+upgrade
on the same Postgres instance does not error if roles already exist.

downgrade() revokes grants and drops the roles in dependency order.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Create roles idempotently (IF NOT EXISTS).
    # Using DO blocks so re-runs on an existing DB do not error.
    # -----------------------------------------------------------------------

    # migrator_user — DDL only; the role Alembic connects as at migration time.
    # Never used by running services.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = 'migrator_user'
            ) THEN
                CREATE ROLE migrator_user WITH LOGIN;
            END IF;
        END
        $$
        """
    )

    # orchestrator_user — orchestrator service runtime role.
    # Full R/W on orchestrator.*; NO access to backend.*.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = 'orchestrator_user'
            ) THEN
                CREATE ROLE orchestrator_user WITH LOGIN;
            END IF;
        END
        $$
        """
    )

    # backend_user — backend service runtime role.
    # R-only on orchestrator.*; full R/W on backend.*.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = 'backend_user'
            ) THEN
                CREATE ROLE backend_user WITH LOGIN;
            END IF;
        END
        $$
        """
    )

    # -----------------------------------------------------------------------
    # Grants for orchestrator_user
    # Access to orchestrator schema: full R/W on all tables.
    # Explicitly NO access to backend schema.
    # -----------------------------------------------------------------------

    # Allow connecting to the database
    op.execute("GRANT CONNECT ON DATABASE traider TO orchestrator_user")

    # orchestrator schema: usage + full DML
    op.execute("GRANT USAGE ON SCHEMA orchestrator TO orchestrator_user")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON ALL TABLES IN SCHEMA orchestrator TO orchestrator_user"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA orchestrator TO orchestrator_user"
    )
    # Default privileges so future tables are automatically accessible
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO orchestrator_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "GRANT USAGE, SELECT ON SEQUENCES TO orchestrator_user"
    )

    # backend schema: explicitly revoke all access (defense-in-depth).
    # orchestrator_user must NOT be able to touch backend.* — it must write
    # orchestrator.nav_snapshots which triggers the backend refresh, but must
    # not be able to directly read or write backend tables.
    op.execute("REVOKE ALL ON SCHEMA backend FROM orchestrator_user")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA backend FROM orchestrator_user")

    # -----------------------------------------------------------------------
    # Grants for backend_user
    # R-only on orchestrator.*; full R/W on backend.*.
    # -----------------------------------------------------------------------

    op.execute("GRANT CONNECT ON DATABASE traider TO backend_user")

    # orchestrator schema: usage + SELECT only (read truth, cannot mutate)
    op.execute("GRANT USAGE ON SCHEMA orchestrator TO backend_user")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA orchestrator TO backend_user")
    op.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA orchestrator TO backend_user")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "GRANT SELECT ON TABLES TO backend_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "GRANT SELECT ON SEQUENCES TO backend_user"
    )

    # backend schema: usage + full R/W (mat-views + ephemeral tables)
    op.execute("GRANT USAGE ON SCHEMA backend TO backend_user")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON ALL TABLES IN SCHEMA backend TO backend_user"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA backend TO backend_user")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA backend "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO backend_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA backend "
        "GRANT USAGE, SELECT ON SEQUENCES TO backend_user"
    )

    # -----------------------------------------------------------------------
    # Grants for migrator_user — DDL authority.
    # Needs full access to both schemas for DDL operations.
    # NEVER used at runtime.
    # -----------------------------------------------------------------------

    op.execute("GRANT CONNECT ON DATABASE traider TO migrator_user")
    op.execute("GRANT ALL ON SCHEMA orchestrator TO migrator_user")
    op.execute("GRANT ALL ON SCHEMA backend TO migrator_user")
    op.execute("GRANT ALL ON ALL TABLES IN SCHEMA orchestrator TO migrator_user")
    op.execute("GRANT ALL ON ALL TABLES IN SCHEMA backend TO migrator_user")
    op.execute("GRANT ALL ON ALL SEQUENCES IN SCHEMA orchestrator TO migrator_user")
    op.execute("GRANT ALL ON ALL SEQUENCES IN SCHEMA backend TO migrator_user")


def downgrade() -> None:
    # Revoke grants then drop roles.
    # Revoke in reverse order: migrator_user → backend_user → orchestrator_user

    # migrator_user revocations
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA backend FROM migrator_user")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA orchestrator FROM migrator_user")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA backend FROM migrator_user")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA orchestrator FROM migrator_user")
    op.execute("REVOKE ALL ON SCHEMA backend FROM migrator_user")
    op.execute("REVOKE ALL ON SCHEMA orchestrator FROM migrator_user")
    op.execute("REVOKE CONNECT ON DATABASE traider FROM migrator_user")

    # backend_user revocations
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA backend "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM backend_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA backend "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM backend_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "REVOKE SELECT ON TABLES FROM backend_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "REVOKE SELECT ON SEQUENCES FROM backend_user"
    )
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA backend FROM backend_user")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA backend FROM backend_user")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA orchestrator FROM backend_user")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA orchestrator FROM backend_user")
    op.execute("REVOKE ALL ON SCHEMA backend FROM backend_user")
    op.execute("REVOKE USAGE ON SCHEMA orchestrator FROM backend_user")
    op.execute("REVOKE CONNECT ON DATABASE traider FROM backend_user")

    # orchestrator_user revocations
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM orchestrator_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA orchestrator "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM orchestrator_user"
    )
    op.execute(
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA orchestrator FROM orchestrator_user"
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA orchestrator FROM orchestrator_user")
    op.execute("REVOKE USAGE ON SCHEMA orchestrator FROM orchestrator_user")
    op.execute("REVOKE CONNECT ON DATABASE traider FROM orchestrator_user")

    # Drop roles (only if they exist — tolerates partial downgrade)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'migrator_user') THEN
                DROP ROLE migrator_user;
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'backend_user') THEN
                DROP ROLE backend_user;
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'orchestrator_user') THEN
                DROP ROLE orchestrator_user;
            END IF;
        END
        $$
        """
    )

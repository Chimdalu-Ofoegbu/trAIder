-- =============================================================================
-- scripts/postgres-init.sql — Bootstrap Postgres roles for trAIder (D-19)
--
-- Runs once at container first-start via docker-entrypoint-initdb.d.
-- Idempotent: uses IF NOT EXISTS / DO $$ checks so re-running is safe.
--
-- Roles created:
--   migrator_user   — DDL only, used exclusively by Alembic; never by services
--   orchestrator_user — R/W on orchestrator.*, NO ACCESS to backend.*
--   backend_user    — SELECT on orchestrator.*, R/W on backend.*
-- =============================================================================

-- Create roles (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'migrator_user') THEN
        CREATE ROLE migrator_user WITH LOGIN PASSWORD 'migrator_pass';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'orchestrator_user') THEN
        CREATE ROLE orchestrator_user WITH LOGIN PASSWORD 'orchestrator_pass';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'backend_user') THEN
        CREATE ROLE backend_user WITH LOGIN PASSWORD 'backend_pass';
    END IF;
END $$;

-- Grant migrator full DDL on the database
GRANT ALL PRIVILEGES ON DATABASE traider TO migrator_user;

-- NOTE: Schema-level grants are applied by Alembic migrations after schemas are created.
-- The roles exist here so that Alembic env.py can reference them in GRANT statements.

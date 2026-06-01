"""Integration tests for the trAIder Alembic migration tree.

Tests:
  1. upgrade head → assert both schemas, tables, ENUM, BRIN index, roles, mat-view exist
  2. downgrade base → assert all trAIder objects are gone (no tables in public)

Skip strategy:
  - If TEST_DATABASE_URL is not set, tests skip with a clear message.
  - If the DB is unreachable (connection refused / auth failure), tests skip.
  - Tests do NOT fail when no Postgres is available — CI without a DB must remain green.

Requirements:
  - TEST_DATABASE_URL must be a synchronous psycopg URL:
      postgresql+psycopg://migrator_user:pass@localhost:5432/traider_test
  - The role running tests must have CREATEDB (or the DB must already exist).

Usage:
  # With a live Postgres (e.g. after `make up`):
  export TEST_DATABASE_URL=postgresql+psycopg://migrator_user:pass@localhost:5432/traider_test
  uv run pytest migrations/tests/test_migrations.py -v

  # Without Postgres (skips):
  uv run pytest migrations/tests/test_migrations.py -v
  # → SKIPPED [reason: No Postgres reachable ...]
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip guard — must be at module level so collection works even without psycopg.
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
SKIP_REASON = ""

if not TEST_DATABASE_URL:
    SKIP_REASON = (
        "No Postgres reachable: TEST_DATABASE_URL environment variable is not set. "
        "Set it to a psycopg URL pointing at a test database to run these integration tests. "
        "Example: postgresql+psycopg://migrator_user:pass@localhost:5432/traider_test"
    )
else:
    # Probe the DB connection before collecting any tests
    try:
        import psycopg  # noqa: F401 — probe import only
        _probe_url = TEST_DATABASE_URL.replace("+psycopg", "", 1).replace("postgresql://", "", 1)
        # Quick connection probe
        _conn_str = TEST_DATABASE_URL.replace("postgresql+psycopg://", "").replace("postgresql://", "")
        # Use a subprocess probe to avoid import-time side effects
        _result = subprocess.run(
            [sys.executable, "-c",
             f"import psycopg; psycopg.connect('{TEST_DATABASE_URL.replace('postgresql+psycopg://', 'postgresql://')}').close(); print('ok')"],
            capture_output=True, text=True, timeout=5,
        )
        if _result.returncode != 0:
            SKIP_REASON = (
                f"No Postgres reachable: connection to TEST_DATABASE_URL failed. "
                f"Error: {_result.stderr.strip()[:200]}"
            )
    except ImportError:
        SKIP_REASON = (
            "psycopg not installed in this environment. "
            "Install it or use the backend venv: uv run --directory backend pytest ..."
        )
    except Exception as e:  # noqa: BLE001
        SKIP_REASON = f"No Postgres reachable: {e}"

_skip_if_no_db = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "no reason")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "migrations" / "alembic.ini"


def _run_alembic(cmd: list[str], db_url: str) -> subprocess.CompletedProcess:
    """Run an alembic command as a subprocess, inheriting the env + overriding DATABASE_URL."""
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *cmd],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(cmd)} failed:\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )
    return result


def _get_connection(db_url: str):
    """Return an open psycopg synchronous connection."""
    import psycopg  # noqa: PLC0415

    # psycopg.connect accepts postgresql:// (not the +psycopg driver prefix)
    clean_url = db_url.replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(clean_url, autocommit=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_url() -> str:
    """Resolved TEST_DATABASE_URL (already guarded by skip mark)."""
    return TEST_DATABASE_URL


@pytest.fixture(scope="module", autouse=False)
def migrated_db(db_url):
    """Run `alembic upgrade head`, yield, then run `alembic downgrade base`.

    This is the core fixture exercising the full upgrade/downgrade cycle.
    """
    # Upgrade
    _run_alembic(["upgrade", "head"], db_url)
    yield db_url
    # Downgrade (always run even if assertions fail)
    _run_alembic(["downgrade", "base"], db_url)


# ---------------------------------------------------------------------------
# Tests — all gated by _skip_if_no_db
# ---------------------------------------------------------------------------

@_skip_if_no_db
class TestUpgradeHead:
    """Assert schema state after `alembic upgrade head`."""

    def test_orchestrator_schema_exists(self, migrated_db):
        """orchestrator schema must exist after upgrade."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'orchestrator'"
            ).fetchone()
        assert result is not None, "orchestrator schema not found after upgrade head"

    def test_backend_schema_exists(self, migrated_db):
        """backend schema must exist after upgrade."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'backend'"
            ).fetchone()
        assert result is not None, "backend schema not found after upgrade head"

    def test_no_traider_tables_in_public(self, migrated_db):
        """No trAIder application tables must land in the public schema (T-0-schema-leak)."""
        known_traider_tables = {
            "sessions", "vaults", "positions", "trades", "journal_entries",
            "model_decisions", "nav_snapshots", "journal_state_log",
            "model_status_log", "event_log",
            "websocket_sessions", "verifier_replay_log",
        }
        with _get_connection(migrated_db) as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            ).fetchall()
        public_tables = {r[0] for r in rows}
        leaked = known_traider_tables & public_tables
        assert not leaked, (
            f"trAIder tables found in public schema (security leak!): {leaked}"
        )

    def test_journal_entries_exists_in_orchestrator(self, migrated_db):
        """journal_entries must be in orchestrator schema."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'orchestrator' AND table_name = 'journal_entries'"
            ).fetchone()
        assert result is not None, "orchestrator.journal_entries not found"

    def test_journal_unique_constraint(self, migrated_db):
        """UNIQUE(vault_address, order_key) constraint must exist on journal_entries."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'orchestrator'
                  AND table_name = 'journal_entries'
                  AND constraint_type = 'UNIQUE'
                  AND constraint_name = 'uq_journal_vault_order'
                """
            ).fetchone()
        assert result is not None, (
            "UNIQUE constraint uq_journal_vault_order not found on orchestrator.journal_entries"
        )

    def test_journal_state_column_type(self, migrated_db):
        """journal_entries.state column must be of type journal_state (ENUM)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT udt_name
                FROM information_schema.columns
                WHERE table_schema = 'orchestrator'
                  AND table_name = 'journal_entries'
                  AND column_name = 'state'
                """
            ).fetchone()
        assert result is not None, "journal_entries.state column not found"
        assert result[0] == "journal_state", (
            f"journal_entries.state has wrong type: expected journal_state, got {result[0]}"
        )

    def test_journal_state_enum_values(self, migrated_db):
        """journal_state ENUM must contain all 7 states (D-21)."""
        expected = {
            "pending_pin", "pinned_primary", "pinned_backup",
            "signed", "submitted", "recorded", "failed",
        }
        with _get_connection(migrated_db) as conn:
            rows = conn.execute(
                """
                SELECT enumlabel
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                JOIN pg_namespace n ON t.typnamespace = n.oid
                WHERE n.nspname = 'orchestrator' AND t.typname = 'journal_state'
                """
            ).fetchall()
        actual = {r[0] for r in rows}
        assert actual == expected, (
            f"journal_state ENUM values mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_brin_index_on_nav_snapshots(self, migrated_db):
        """BRIN index ix_nav_brin must exist on orchestrator.nav_snapshots (D-20)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'orchestrator'
                  AND tablename = 'nav_snapshots'
                  AND indexname = 'ix_nav_brin'
                """
            ).fetchone()
        assert result is not None, "BRIN index ix_nav_brin not found on orchestrator.nav_snapshots"
        assert "brin" in result[1].lower(), (
            f"ix_nav_brin is not a BRIN index: {result[1]}"
        )

    def test_brin_index_on_trades(self, migrated_db):
        """BRIN index ix_trades_brin must exist on orchestrator.trades (D-20)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'orchestrator'
                  AND tablename = 'trades'
                  AND indexname = 'ix_trades_brin'
                """
            ).fetchone()
        assert result is not None, "BRIN index ix_trades_brin not found on orchestrator.trades"
        assert "brin" in result[1].lower(), (
            f"ix_trades_brin is not a BRIN index: {result[1]}"
        )

    def test_btree_index_on_trades_hash(self, migrated_db):
        """B-tree index ix_trades_hash must exist on orchestrator.trades(trade_hash)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'orchestrator'
                  AND tablename = 'trades'
                  AND indexname = 'ix_trades_hash'
                """
            ).fetchone()
        assert result is not None, "B-tree index ix_trades_hash not found on orchestrator.trades"

    def test_dashboard_model_state_is_materialized_view(self, migrated_db):
        """backend.dashboard_model_state must be a MATERIALIZED VIEW."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT matviewname
                FROM pg_matviews
                WHERE schemaname = 'backend'
                  AND matviewname = 'dashboard_model_state'
                """
            ).fetchone()
        assert result is not None, "backend.dashboard_model_state is not a materialized view"

    def test_dashboard_session_state_is_materialized_view(self, migrated_db):
        """backend.dashboard_session_state must be a MATERIALIZED VIEW."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT matviewname
                FROM pg_matviews
                WHERE schemaname = 'backend'
                  AND matviewname = 'dashboard_session_state'
                """
            ).fetchone()
        assert result is not None, "backend.dashboard_session_state is not a materialized view"

    def test_nav_refresh_trigger_exists(self, migrated_db):
        """trg_refresh_model_state trigger must exist on orchestrator.nav_snapshots."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                """
                SELECT trigger_name
                FROM information_schema.triggers
                WHERE event_object_schema = 'orchestrator'
                  AND event_object_table = 'nav_snapshots'
                  AND trigger_name = 'trg_refresh_model_state'
                """
            ).fetchone()
        assert result is not None, (
            "trigger trg_refresh_model_state not found on orchestrator.nav_snapshots"
        )

    def test_orchestrator_user_role_exists(self, migrated_db):
        """orchestrator_user role must exist after upgrade (D-19)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = 'orchestrator_user'"
            ).fetchone()
        assert result is not None, "orchestrator_user role not found"

    def test_backend_user_role_exists(self, migrated_db):
        """backend_user role must exist after upgrade (D-19)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = 'backend_user'"
            ).fetchone()
        assert result is not None, "backend_user role not found"

    def test_migrator_user_role_exists(self, migrated_db):
        """migrator_user role must exist after upgrade (D-19)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = 'migrator_user'"
            ).fetchone()
        assert result is not None, "migrator_user role not found"

    def test_alembic_version_in_orchestrator_schema(self, migrated_db):
        """alembic_version table must be in orchestrator schema (Assumption A1, D-22)."""
        with _get_connection(migrated_db) as conn:
            result = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'orchestrator' AND table_name = 'alembic_version'"
            ).fetchone()
        assert result is not None, (
            "alembic_version table not in orchestrator schema — version_table_schema not applied"
        )

    def test_all_orchestrator_tables_exist(self, migrated_db):
        """All 10 orchestrator.* tables must exist after upgrade."""
        expected_tables = {
            "sessions", "vaults", "positions", "trades", "journal_entries",
            "model_decisions", "nav_snapshots", "journal_state_log",
            "model_status_log", "event_log",
        }
        with _get_connection(migrated_db) as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'orchestrator' AND table_type = 'BASE TABLE'"
            ).fetchall()
        actual = {r[0] for r in rows} - {"alembic_version"}
        missing = expected_tables - actual
        assert not missing, f"Missing orchestrator tables: {missing}"

    def test_all_backend_tables_exist(self, migrated_db):
        """websocket_sessions and verifier_replay_log must exist in backend schema."""
        expected_tables = {"websocket_sessions", "verifier_replay_log"}
        with _get_connection(migrated_db) as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'backend' AND table_type = 'BASE TABLE'"
            ).fetchall()
        actual = {r[0] for r in rows}
        missing = expected_tables - actual
        assert not missing, f"Missing backend tables: {missing}"


@_skip_if_no_db
class TestDowngradeBase:
    """Assert all trAIder objects are removed after `alembic downgrade base`.

    NOTE: The migrated_db fixture runs upgrade head THEN downgrade base as teardown.
    These tests verify the downgrade by inspecting state AFTER the fixture tears down.
    We use a separate fixture that runs downgrade independently.
    """

    @pytest.fixture(autouse=True)
    def run_full_cycle(self, db_url):
        """Run full upgrade → assertions (in TestUpgradeHead) → downgrade cycle."""
        _run_alembic(["upgrade", "head"], db_url)
        yield
        _run_alembic(["downgrade", "base"], db_url)

    def test_orchestrator_tables_gone_after_downgrade(self, db_url):
        """All orchestrator.* tables must be gone after downgrade base."""
        traider_tables = {
            "sessions", "vaults", "positions", "trades", "journal_entries",
            "model_decisions", "nav_snapshots", "journal_state_log",
            "model_status_log", "event_log",
        }
        with _get_connection(db_url) as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'orchestrator'"
            ).fetchall()
        remaining = {r[0] for r in rows} & traider_tables
        assert not remaining, (
            f"orchestrator tables still present after downgrade base: {remaining}"
        )

    def test_backend_tables_gone_after_downgrade(self, db_url):
        """All backend.* tables must be gone after downgrade base."""
        traider_tables = {"websocket_sessions", "verifier_replay_log"}
        with _get_connection(db_url) as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'backend'"
            ).fetchall()
        remaining = {r[0] for r in rows} & traider_tables
        assert not remaining, (
            f"backend tables still present after downgrade base: {remaining}"
        )

    def test_journal_state_enum_gone_after_downgrade(self, db_url):
        """journal_state ENUM must be dropped after downgrade base."""
        with _get_connection(db_url) as conn:
            result = conn.execute(
                """
                SELECT typname FROM pg_type t
                JOIN pg_namespace n ON t.typnamespace = n.oid
                WHERE n.nspname = 'orchestrator' AND t.typname = 'journal_state'
                """
            ).fetchone()
        assert result is None, (
            "journal_state ENUM still exists after downgrade base — drop failed"
        )

    def test_materialized_views_gone_after_downgrade(self, db_url):
        """Materialized views must not exist after downgrade base."""
        with _get_connection(db_url) as conn:
            rows = conn.execute(
                "SELECT matviewname FROM pg_matviews "
                "WHERE schemaname = 'backend'"
            ).fetchall()
        remaining = {r[0] for r in rows}
        assert not remaining, (
            f"backend mat-views still present after downgrade base: {remaining}"
        )

    def test_trigger_gone_after_downgrade(self, db_url):
        """trg_refresh_model_state trigger must be gone after downgrade base."""
        with _get_connection(db_url) as conn:
            rows = conn.execute(
                """
                SELECT trigger_name FROM information_schema.triggers
                WHERE event_object_schema = 'orchestrator'
                  AND trigger_name = 'trg_refresh_model_state'
                """
            ).fetchall()
        assert not rows, (
            "trg_refresh_model_state trigger still exists after downgrade base"
        )

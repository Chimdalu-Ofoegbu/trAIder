"""Integration test for CR-01: streak state persists and rehydrates across restart.

Test: record a status with api_failure_streak=2, malformed_streak=1; build a FRESH
FailureTracker (simulating restart); rehydrate from DB; assert both streaks restored.

Requires a live Postgres instance reachable at ORCHESTRATOR_DATABASE_URL.
Skips cleanly when Postgres is not available.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.loop.failure_tracker import FailureTracker
from orchestrator.state.db import get_engine, get_latest_model_status, record_model_status

# ---------------------------------------------------------------------------
# Fixture: async DB session (skip if Postgres unavailable)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pg_db():
    """Async DB session connected to orchestrator DB.  Skips if not reachable."""
    import os

    db_url = os.environ.get("ORCHESTRATOR_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("ORCHESTRATOR_DATABASE_URL not set — skipping integration test")

    try:
        engine = get_engine(echo=False)
        async with AsyncSession(engine) as session:
            # Connectivity check
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
            yield session
        await engine.dispose()
    except Exception as exc:
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip(f"Postgres not reachable: {exc}")
        raise


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaks_persist_and_rehydrate_across_restart(pg_db: AsyncSession) -> None:
    """Record streaks → simulate restart → rehydrate → assert streaks match.

    CR-01 regression test (ORCH-06 restart-safety).
    """
    vault = f"0x{uuid.uuid4().hex[:40]}"  # unique vault per test run
    session_id = str(uuid.uuid4())

    # We need an orchestrator.sessions row for the FK
    from sqlalchemy import text

    await pg_db.execute(
        text(
            """
            INSERT INTO orchestrator.sessions
                (id, session_key, duration_seconds, state, started_at, created_at, updated_at)
            VALUES
                (CAST(:session_id AS uuid), :session_key, 900,
                 'active', NOW(), NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"session_id": session_id, "session_key": f"test-session-{session_id[:8]}"},
    )
    await pg_db.commit()

    # Build a tracker with known state (simulating 2/3 of the way to api pause)
    original_tracker = FailureTracker()
    original_tracker.api_failure_streak = 2
    original_tracker.malformed_streak = 1
    # Should NOT be paused yet (threshold is 3 for api, 5 for malformed)
    assert not original_tracker.should_pause()

    # Persist the tracker state
    await record_model_status(
        pg_db,
        vault_address=vault,
        session_id=session_id,
        model="claude-opus-4-7",
        status="active",
        consecutive_failures=original_tracker.consecutive(),
        api_failure_streak=original_tracker.api_failure_streak,
        malformed_streak=original_tracker.malformed_streak,
        reason="test record for CR-01 rehydration",
        cycle_number=5,
    )

    # Simulate SIGKILL + restart: build a FRESH tracker (zeroed)
    rehydrated_tracker = FailureTracker()
    assert rehydrated_tracker.api_failure_streak == 0
    assert rehydrated_tracker.malformed_streak == 0

    # Rehydrate from DB (same logic as run_session startup)
    latest = await get_latest_model_status(pg_db, vault_address=vault)
    assert latest is not None, "Expected a model_status_log row after record_model_status"

    api_streak = latest.get("api_failure_streak") or 0
    mal_streak = latest.get("malformed_streak") or 0
    rehydrated_tracker.api_failure_streak = api_streak
    rehydrated_tracker.malformed_streak = mal_streak

    # Verify both streaks restored
    assert rehydrated_tracker.api_failure_streak == 2, (
        f"Expected api_failure_streak=2 after rehydration, got {rehydrated_tracker.api_failure_streak}. "
        "CR-01 regression: streak was not persisted to DB."
    )
    assert rehydrated_tracker.malformed_streak == 1, (
        f"Expected malformed_streak=1 after rehydration, got {rehydrated_tracker.malformed_streak}. "
        "CR-01 regression: streak was not persisted to DB."
    )
    # Should still not be paused (2 < 3)
    assert not rehydrated_tracker.should_pause(), (
        "Tracker should not be paused at api_failure_streak=2 (threshold=3)"
    )

    # Also verify the model column was written correctly
    assert latest["model"] == "claude-opus-4-7", (
        f"Expected model='claude-opus-4-7', got {latest['model']!r}. CR-01 regression."
    )


@pytest.mark.asyncio
async def test_paused_tracker_rehydrates_with_paused_true(pg_db: AsyncSession) -> None:
    """A tracker at pause threshold rehydrates with paused=True after restart."""
    vault = f"0x{uuid.uuid4().hex[:40]}"
    session_id = str(uuid.uuid4())

    from sqlalchemy import text

    await pg_db.execute(
        text(
            """
            INSERT INTO orchestrator.sessions
                (id, session_key, duration_seconds, state, started_at, created_at, updated_at)
            VALUES
                (CAST(:session_id AS uuid), :session_key, 900,
                 'active', NOW(), NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"session_id": session_id, "session_key": f"test-session-{session_id[:8]}"},
    )
    await pg_db.commit()

    # Tracker at pause threshold
    paused_tracker = FailureTracker()
    paused_tracker.api_failure_streak = 3  # exactly at API_FAILURE_PAUSE_THRESHOLD
    paused_tracker.paused = True

    await record_model_status(
        pg_db,
        vault_address=vault,
        session_id=session_id,
        model="claude-opus-4-7",
        status="paused",
        consecutive_failures=paused_tracker.consecutive(),
        api_failure_streak=paused_tracker.api_failure_streak,
        malformed_streak=paused_tracker.malformed_streak,
        reason="pause threshold reached",
        cycle_number=3,
    )

    # Simulate restart
    rehydrated = FailureTracker()
    latest = await get_latest_model_status(pg_db, vault_address=vault)
    assert latest is not None

    from orchestrator.loop.failure_tracker import (
        API_FAILURE_PAUSE_THRESHOLD,
        MALFORMED_PAUSE_THRESHOLD,
    )

    rehydrated.api_failure_streak = latest.get("api_failure_streak") or 0
    rehydrated.malformed_streak = latest.get("malformed_streak") or 0
    if (
        rehydrated.api_failure_streak >= API_FAILURE_PAUSE_THRESHOLD
        or rehydrated.malformed_streak >= MALFORMED_PAUSE_THRESHOLD
    ):
        rehydrated.paused = True

    assert rehydrated.api_failure_streak == 3
    assert rehydrated.should_pause(), (
        "Rehydrated tracker should be paused when api_failure_streak=3 (threshold=3)"
    )

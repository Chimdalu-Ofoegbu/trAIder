"""Shared fixtures for orchestrator unit tests (Phase 2).

Fixtures:
  session_config  -- minimal SessionConfig-shaped object for unit tests.
                     execution_delay_cycles=1 (the D-14 safe default).
  enforce_delay_gte_1 -- D-14 GUARD: restart-safety tests MUST opt-in to this
                         fixture; will pytest.fail (not skip) if delay < 1.

D-14 rationale (from 02-CONTEXT.md / 02-VALIDATION.md):
  At executionDelayCycles=0 the async pending-order window is bypassed entirely
  (no time for a SIGKILL between record-intent and executeOrder). A restart-safety
  test running at delay=0 would always pass vacuously -- it never encounters the
  condition it is meant to test. The guard converts that silent false-green into a
  loud, un-ignorable failure so bad configs cannot sneak through CI.

  Note: the guard is NOT autouse. Unit tests that don't test restart-safety may
  run at delay=0 legitimately (e.g., malformed-path or pause-on-3-failures tests
  that don't involve the pending-order state machine at all).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _SessionConfig:
    """Minimal SessionConfig-shaped value object for unit tests.

    Fields match the loop/session.py analogs used by the driver:
      execution_delay_cycles  -- blocks to wait between record-intent and submit
      session_duration_seconds -- wall-clock length of the trading session
      cadence_seconds          -- interval between trading cycles
      price_seed               -- RNG seed for the seeded price walk
      session_id               -- UUID string for DB FK
    """

    execution_delay_cycles: int = 1
    session_duration_seconds: int = 60
    cadence_seconds: float = 1.0
    price_seed: int = 42
    session_id: str = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def session_config() -> _SessionConfig:
    """Return a default SessionConfig suitable for unit tests.

    execution_delay_cycles is 1 (the minimum safe value for restart-safety paths).
    Unit tests that need delay=0 may override this fixture locally.
    """
    return _SessionConfig()


@pytest.fixture
def enforce_delay_gte_1(session_config: _SessionConfig) -> _SessionConfig:
    """D-14 GUARD: restart-safety tests MUST run at executionDelayCycles >= 1.

    Opts the calling test into the D-14 correctness contract:
      - delay=0 bypasses the async pending-order window and would pass vacuously.
      - This fixture fails loudly (pytest.fail, NOT skip) so the bad config is
        un-ignorable in CI.

    Usage: declare `enforce_delay_gte_1` as a parameter of any test that exercises
    the SIGKILL-resume or record-intent-before-submit code paths (SC-2 / ORCH-07/08).
    """
    if session_config.execution_delay_cycles < 1:
        pytest.fail(
            "D-14 VIOLATION: restart-safety test running at executionDelayCycles=0. "
            "This bypasses the async pending-order window and would pass vacuously."
        )
    return session_config
